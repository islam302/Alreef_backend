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


def calculate_totals(*, cart, address, shipping_method_id, payment_method: str, coupon_code: str = "") -> CheckoutTotals:
    items = list(cart.items.select_related("variant__product").all())
    if not items:
        raise ApplicationError(ErrorCodes.CART_EMPTY, "Cart is empty.")

    currency = cart.currency
    subtotal = sum((i.line_total for i in items), start=Money(0, currency))

    from apps.promotions.services import compute_promotions_for_items

    promotion_discount, applied_promotions, promo_free_shipping = compute_promotions_for_items(items, currency)

    coupon_discount = Money(0, currency)
    if coupon_code:
        from apps.coupons.services import compute_discount, get_valid_coupon

        coupon = get_valid_coupon(coupon_code)
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
    notes: str = "", idempotency_key: str = "",
):
    """
    Turn the cart into an Order and start its payment.

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

    totals = calculate_totals(
        cart=cart, address=address, shipping_method_id=shipping_method_id, payment_method=payment_method, coupon_code=coupon_code,
    )

    shipping_method = ShippingMethod.objects.filter(pk=shipping_method_id).first() if shipping_method_id else None

    order = create_order_with_unique_number(
        user=user,
        currency=cart.currency,
        customer_name=address.full_name,
        customer_email=user.email,
        customer_phone=str(address.phone),
        shipping_address_snapshot={
            "label": address.label, "full_name": address.full_name, "phone": str(address.phone),
            "governorate": address.governorate.name_en, "city": address.city.name_en if address.city else "",
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

    return order, payment


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
    from apps.notifications.tasks import send_email_task
    from apps.orders.services import transition_status

    if order.status == OrderStatus.PENDING:
        order = transition_status(order, OrderStatus.CONFIRMED)

    for reservation in StockReservation.objects.filter(order=order, status=StockReservationStatus.ACTIVE):
        consume_reservation(reservation)

    if order.coupon_code:
        from apps.coupons.services import get_valid_coupon, record_usage

        try:
            coupon = get_valid_coupon(order.coupon_code)
            record_usage(coupon=coupon, user=order.user, order=order, discount_amount=order.coupon_discount)
        except ApplicationError:
            pass  # Coupon may have been deactivated after order placement — usage tracking is best-effort.

    send_email_task.delay(
        to=order.customer_email, subject="Order confirmed", template_name="order_confirmation",
        context={"order_number": order.order_number, "total": str(order.grand_total.amount), "currency": order.currency},
        event="order_created", user_id=order.user_id,
    )
    if payment_received:
        send_email_task.delay(
            to=order.customer_email, subject="Payment received", template_name="payment_confirmation",
            context={"order_number": order.order_number, "amount": str(order.grand_total.amount), "currency": order.currency},
            event="payment_success", user_id=order.user_id,
        )

    try:
        from apps.loyalty.services import award_points_for_order

        award_points_for_order(order)
    except ImportError:
        pass
    return order
