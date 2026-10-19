import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from djmoney.money import Money

from apps.carts.models import CartStatus
from apps.catalog.models import TaxCategory
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.orders.models import Order, OrderItem, OrderStatus, PaymentMethodSnapshot
from apps.shipping.services import get_quote_for_method

logger = logging.getLogger(__name__)

# The method -> route mapping used to live here as a dict. It now lives in
# apps/payments/config.py, next to the configuration that decides whether each
# method can be offered at all, because the two facts were drifting — and
# because one method (InstaPay) has two possible routes, which a flat dict
# cannot express.


@dataclass
class CheckoutTotals:
    subtotal: Money
    coupon_discount: Money
    promotion_discount: Money
    tax_total: Money
    shipping_total: Money
    cod_fee: Money
    grand_total: Money
    applied_promotions: list = field(default_factory=list)
    free_shipping: bool = False


def tax_rate_for_product(product) -> Decimal:
    if product.tax_category == TaxCategory.STANDARD:
        return settings.EGYPT_VAT_RATE
    return Decimal("0")


def _refresh_cart_lines(items):
    """
    Hold every line to what the store sells *today*.

    A cart line remembers the price it was added at, and nothing re-read it
    before checkout. So a customer who carted an item during a sale — or while
    a pricing mistake was live — could keep that price indefinitely, and an item
    staff had withdrawn (variant disabled, product unpublished or archived) could
    still be bought from a cart that already held it.
    """
    from apps.catalog.models import ProductStatus

    for item in items:
        variant = item.variant
        product = variant.product
        if not variant.is_active or not product.published or product.status != ProductStatus.ACTIVE:
            raise ApplicationError(
                ErrorCodes.VARIANT_NOT_FOUND,
                f"'{product.name_en}' is no longer available. Please remove it from your cart.",
            )
        if item.unit_price_snapshot != variant.price:
            item.unit_price_snapshot = variant.price
            item.save(update_fields=["unit_price_snapshot"])


def calculate_totals(
    *, cart, address, shipping_method_id, payment_method: str, coupon_code: str = "", user=None,
    phone: str = "",
) -> CheckoutTotals:
    """
    `phone` identifies a guest for the coupon's per-customer limit; a signed-in
    customer is identified by `user` and the phone is ignored.
    """
    items = list(cart.items.select_related("variant__product").all())
    if not items:
        raise ApplicationError(ErrorCodes.CART_EMPTY, "Cart is empty.")
    _refresh_cart_lines(items)

    currency = cart.currency
    subtotal = sum((i.line_total for i in items), start=Money(0, currency))

    from apps.promotions.services import compute_promotions_for_items

    promotion_discount, applied_promotions, promo_free_shipping = compute_promotions_for_items(items, currency)

    coupon_discount = Money(0, currency)
    if coupon_code:
        from apps.coupons.services import compute_discount, validate_coupon_for_cart

        # The full cart check, not just "does this code exist": the code comes
        # from the request body here, so the per-user limit, the minimum order
        # and the coupon's scope have to be enforced here too — otherwise the
        # cart's coupon endpoint is the only place they apply, and checkout
        # never asks it.
        coupon = validate_coupon_for_cart(code=coupon_code, cart=cart, user=user, phone="" if user else phone)
        coupon_discount = compute_discount(coupon=coupon, items=items, subtotal=subtotal)
        if coupon.discount_type == "free_shipping":
            promo_free_shipping = True

    discounted_subtotal = subtotal - coupon_discount - promotion_discount
    if discounted_subtotal.amount < 0:
        discounted_subtotal = Money(0, currency)

    tax_total = Money(0, currency)
    for item in items:
        rate = tax_rate_for_product(item.variant.product)
        if rate > 0:
            tax_total += item.line_total * rate

    shipping_total = Money(0, currency)
    if shipping_method_id:
        quote = get_quote_for_method(governorate=address.governorate, method_id=shipping_method_id, subtotal=subtotal)
        shipping_total = Money(0, currency) if promo_free_shipping else quote.price

    # Every method is checked for availability, not just COD: a method whose
    # gateway is switched off or half-configured must fail here, while the
    # customer is still on the checkout page, rather than after the order row
    # exists.
    from apps.payments import config as payment_config

    availability = payment_config.availability(payment_method)
    if not availability.available:
        # `availability.reason` names our own configuration, so it is logged
        # rather than returned. The customer gets the method label.
        logger.warning("checkout rejected method '%s': %s", payment_method, availability.reason)
        label = availability.spec.label_en if availability.spec else payment_method
        raise ApplicationError(
            ErrorCodes.PAYMENT_METHOD_NOT_AVAILABLE,
            f"{label} is not available at the moment. Please choose another payment method.",
        )

    cod_fee = Money(0, currency)
    if payment_method == PaymentMethodSnapshot.COD:
        fee, minimum, maximum = payment_config.cod_limits()
        cod_fee = Money(fee, currency)

        # Checked against the order's own value, before the COD fee is added —
        # a minimum that the fee itself satisfies would be no minimum at all.
        payable = (discounted_subtotal + tax_total + shipping_total).amount
        if minimum and payable < minimum:
            raise ApplicationError(
                ErrorCodes.PAYMENT_METHOD_NOT_AVAILABLE,
                f"Cash on delivery needs an order of at least {minimum} {currency}.",
            )
        if maximum and payable > maximum:
            raise ApplicationError(
                ErrorCodes.PAYMENT_METHOD_NOT_AVAILABLE,
                f"Cash on delivery is not available above {maximum} {currency}.",
            )

        allowed_shipping = payment_config.cod_allowed_shipping_method_ids()
        if allowed_shipping is not None and shipping_method_id and int(shipping_method_id) not in allowed_shipping:
            raise ApplicationError(
                ErrorCodes.PAYMENT_METHOD_NOT_AVAILABLE,
                "The selected delivery option cannot be paid for on delivery.",
            )

    grand_total = discounted_subtotal + tax_total + shipping_total + cod_fee

    return CheckoutTotals(
        subtotal=subtotal, coupon_discount=coupon_discount, promotion_discount=promotion_discount,
        tax_total=tax_total, shipping_total=shipping_total, cod_fee=cod_fee, grand_total=grand_total,
        applied_promotions=applied_promotions, free_shipping=promo_free_shipping,
    )


@transaction.atomic
def create_order_from_cart(
    *, user, cart, address, shipping_method_id, payment_method: str, coupon_code: str = "",
    notes: str = "", idempotency_key: str = "", contact_email: str = "",
):
    """
    Turn the cart into an Order and start its payment.

    `user` is None for a guest checkout. `address` is then an unsaved Address
    built from the request (a guest has no address book), and `contact_email`
    is the optional address they gave; a signed-in customer's email always
    comes from their account.

    `idempotency_key` is the checkout key from the request header. It is passed
    down to the Payment so that a retry which slips past the cache-backed
    mixin — because the cache was flushed, or because two requests raced — still
    cannot produce a second charge: the Payment table has a unique constraint on
    it.
    """
    from apps.carts.models import Cart
    from apps.inventory.services import reserve_stock
    from apps.orders.services import create_order_with_unique_number
    from apps.shipping.models import ShippingMethod

    # Take the cart under a row lock before anything reads it, and only accept
    # one that is still OPEN.
    #
    # The three guards that already existed all key on the request rather than
    # on the cart, and a second submission carrying a different Idempotency-Key
    # walked past every one of them: the mixin's cache lock is keyed on the
    # header, and Payment's unique constraint on `idempotency_key` only catches
    # a repeat of the *same* key. Nothing re-read the cart's status, and it is
    # set to CONVERTED at the end of this function — far too late to exclude a
    # request that is already inside it.
    #
    # So one cart could become two orders: stock deducted twice, two COD
    # liabilities, and later two payment intentions, for a customer who clicked
    # once in two tabs. The cart is what is being spent, so the cart is what has
    # to be serialised.
    cart = Cart.objects.select_for_update().get(pk=cart.pk)
    if cart.status != CartStatus.OPEN:
        raise ApplicationError(
            ErrorCodes.CART_ALREADY_CHECKED_OUT,
            "This cart has already been checked out.",
        )

    items = list(cart.items.select_related("variant__product").all())
    if not items:
        raise ApplicationError(ErrorCodes.CART_EMPTY, "Cart is empty.")
    _refresh_cart_lines(items)

    coupon = None
    if coupon_code:
        # Serialise checkouts spending the same coupon. Its limits are counted
        # from CouponUsage rows, and two checkouts that both read the count
        # before either wrote one each saw room for themselves — a single-use
        # code spent twice. Held until this transaction commits, so the second
        # checkout reads the first one's usage.
        from apps.coupons.models import Coupon

        coupon = Coupon.objects.select_for_update().filter(code__iexact=coupon_code).first()

    phone = str(address.phone)
    totals = calculate_totals(
        cart=cart, address=address, shipping_method_id=shipping_method_id, payment_method=payment_method,
        coupon_code=coupon_code, user=user, phone=phone,
    )

    shipping_method = ShippingMethod.objects.filter(pk=shipping_method_id).first() if shipping_method_id else None

    order = create_order_with_unique_number(
        user=user,
        currency=cart.currency,
        customer_name=address.full_name,
        customer_email=user.email if user else (contact_email or ""),
        customer_phone=phone,
        shipping_address_snapshot={
            "label": address.label, "full_name": address.full_name, "phone": phone,
            # Arabic for everything people read (the order page, tracking, the
            # invoice); the English names kept beside them for the card gateway.
            "governorate": address.governorate.name_ar or address.governorate.name_en,
            "city": (address.city.name_ar or address.city.name_en) if address.city else "",
            "governorate_en": address.governorate.name_en,
            "city_en": address.city.name_en if address.city else "",
            "district": address.district, "street": address.street, "building": address.building,
            "floor": address.floor, "apartment": address.apartment, "landmark": address.landmark,
            "postal_code": address.postal_code, "notes": address.notes,
        },
        shipping_method_name=shipping_method.name if shipping_method else "",
        payment_method=payment_method,
        subtotal=totals.subtotal,
        coupon_code=coupon_code,
        coupon_discount=totals.coupon_discount,
        promotion_discount=totals.promotion_discount,
        tax_total=totals.tax_total,
        shipping_total=totals.shipping_total,
        cod_fee=totals.cod_fee,
        grand_total=totals.grand_total,
        notes=notes,
    )

    if coupon is not None:
        # The use is reserved now, while the coupon row is still locked, not when
        # the order is confirmed. Recording it at confirmation left a gap as long
        # as a card payment takes: every pending order was invisible to the
        # limit, so ten customers could all start paying with a code capped at
        # one — and the confirmations then skipped the record once the cap was
        # hit, so the extra uses were never even counted. Cancelling the order
        # gives the use back (orders.services.cancel_order).
        from apps.coupons.services import record_usage

        record_usage(coupon=coupon, user=user, order=order, discount_amount=totals.coupon_discount)

    for item in items:
        reserve_stock(variant=item.variant, quantity=item.quantity, order=order)
        OrderItem.objects.create(
            order=order, variant=item.variant, product=item.variant.product,
            product_name_snapshot=item.variant.product.name_en, variant_name_snapshot=item.variant.name_suffix,
            sku_snapshot=item.variant.sku, unit_price=item.unit_price_snapshot, quantity=item.quantity,
            line_total=item.line_total,
        )

    from apps.payments import config as payment_config
    from apps.payments.services import create_payment_for_order

    provider_code = payment_config.gateway_for_method(payment_method)

    try:
        payment = create_payment_for_order(
            order=order,
            provider_code=provider_code,
            method=payment_method,
            idempotency_key=idempotency_key,
        )
    except NotImplementedError as exc:
        # A disabled or unconfigured gateway. The adapter's message names our
        # own settings, so it is logged rather than returned to the customer.
        logger.warning("checkout: gateway '%s' unavailable: %s", provider_code, exc)
        raise ApplicationError(
            ErrorCodes.PAYMENT_PROVIDER_UNAVAILABLE,
            "That payment method is not available at the moment. Please choose another.",
        ) from exc

    if payment.order_id != order.id:
        # create_payment_for_order hands back an existing Payment for a key it
        # has already seen, whichever order that Payment belongs to. A key
        # reused across two different checkouts would otherwise attach somebody
        # else's payment — its reference, its redirect — to this response. The
        # order rolls back with the error.
        raise ApplicationError(
            ErrorCodes.IDEMPOTENCY_KEY_CONFLICT,
            "Idempotency-Key was already used for another order.",
        )

    cart.status = CartStatus.CONVERTED
    cart.save(update_fields=["status"])

    if payment_method == PaymentMethodSnapshot.COD:
        # A COD order is confirmed but NOT paid. It used to be captured right
        # here, which said the money was ours before a courier had left the
        # building — and, because `captured` is the state refunds are issued
        # from, made it possible to "refund" cash nobody had collected. The
        # capture now happens in payments.services.record_cod_collection when
        # somebody records the collection.
        confirm_order(order, payment_received=False)
        payment.refresh_from_db()
        order.refresh_from_db()
    elif payment.provider in TRANSFER_ROUTES and payment.status == "requires_action":
        _email_transfer_details(order, payment)

    return order, payment


#: Routes where the customer sends the money themselves and a person confirms it.
TRANSFER_ROUTES = ("instapay", "vodafone_cash")


def _email_transfer_details(order, payment):
    """
    Where to send the money, by email as well as on screen.

    A customer who closes the confirmation page before writing down the wallet
    number or InstaPay address has no other way to pay, and phones us. After
    commit, so a checkout that rolls back sends nothing.
    """
    from apps.notifications.services import queue_email
    from apps.orders.services import order_email_context

    metadata = payment.metadata or {}
    queue_email(
        to=order.customer_email,
        subject="payment_instructions",
        template_name="payment_instructions",
        context={
            **order_email_context(order),
            "route": payment.provider,
            "pay_to": metadata.get("wallet_number") or metadata.get("instapay_address", ""),
            "account_name": metadata.get("wallet_account_name") or metadata.get("instapay_account_name", ""),
            "amount": metadata.get("transfer_amount") or str(payment.amount.amount),
            "reference": metadata.get("transfer_reference", order.order_number),
            "confirm_window_hours": metadata.get("confirm_window_hours", 24),
        },
        event="order_created",
        user_id=order.user_id,
    )


@transaction.atomic
def confirm_order_after_payment(order: Order):
    """The money arrived. Confirm the order and tell the customer both things."""
    return confirm_order(order, payment_received=True)


@transaction.atomic
def confirm_order(order: Order, *, payment_received: bool = True):
    """
    Move a paid-or-promised order into CONFIRMED and do the work that follows.

    `payment_received=False` is the cash-on-delivery case: the order is real,
    the stock is committed, the confirmation email goes out — but no "payment
    received" email does, because no payment has been. Sending one anyway was
    the visible half of the COD bug; a customer would be told we had their money
    while the parcel had not left.
    """
    from apps.inventory.models import StockReservation, StockReservationStatus
    from apps.inventory.services import consume_reservation
    from apps.notifications.services import queue_email
    from apps.orders.services import order_email_context, transition_status

    # The "order confirmed" email is sent by the transition itself
    # (orders.services.STATUS_EMAILS), so it goes out once, on the move to
    # CONFIRMED, however many times this function is reached for the order.
    if order.status == OrderStatus.PENDING:
        order = transition_status(order, OrderStatus.CONFIRMED)

    for reservation in StockReservation.objects.filter(order=order, status=StockReservationStatus.ACTIVE):
        consume_reservation(reservation)

    if order.coupon_code:
        # The use is reserved at checkout. This covers orders placed before
        # that was true and still being paid for: record what was promised
        # without re-validating — the customer already has the discount, and
        # refusing to count it would only hide it from the limit.
        from apps.coupons.models import Coupon, CouponUsage
        from apps.coupons.services import record_usage

        if not CouponUsage.objects.filter(order=order).exists():
            coupon = Coupon.objects.filter(code__iexact=order.coupon_code).first()
            if coupon is not None:
                record_usage(coupon=coupon, user=order.user, order=order, discount_amount=order.coupon_discount)

    # Not for cash on delivery: that money is handed to the courier at the
    # door, and this function runs again when the collection is recorded — a
    # "payment received" email after the "delivered" one is noise. Asked of the
    # payment that was captured rather than the order's method snapshot, because
    # the payment is what actually arrived.
    from apps.payments.models import PaymentStatus

    paid_in_cash = order.payments.filter(provider="cod", status=PaymentStatus.CAPTURED).exists()
    if payment_received and not paid_in_cash:
        context = order_email_context(order)
        context["amount"] = context["total"]
        queue_email(
            to=order.customer_email, subject="payment_confirmation", template_name="payment_confirmation",
            context=context, event="payment_success", user_id=order.user_id,
        )

    if order.user_id is not None:
        # Points belong to an account. A guest has none to hold them.
        try:
            from apps.loyalty.services import award_points_for_order

            award_points_for_order(order)
        except ImportError:
            pass
    return order
