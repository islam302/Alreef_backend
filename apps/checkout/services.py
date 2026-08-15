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

PAYMENT_METHOD_PROVIDER_MAP = {
    PaymentMethodSnapshot.COD: "cod",
    PaymentMethodSnapshot.CARD: "paymob",
    PaymentMethodSnapshot.MOBILE_WALLET: "paymob",
    PaymentMethodSnapshot.FAWRY_PAY: "fawry",
    PaymentMethodSnapshot.VALU: "valu",
    PaymentMethodSnapshot.SYMPL: "sympl",
    PaymentMethodSnapshot.AMAN: "aman",
}


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

    cod_fee = Money(0, currency)
    if payment_method == PaymentMethodSnapshot.COD:
        cod_config = settings.PAYMENT_PROVIDERS["COD"]
        if not cod_config["ENABLED"]:
            raise ApplicationError(ErrorCodes.PAYMENT_PROVIDER_UNAVAILABLE, "Cash on delivery is not available.")
        cod_fee = Money(cod_config["FEE_AMOUNT"], currency)

    grand_total = discounted_subtotal + tax_total + shipping_total + cod_fee

    return CheckoutTotals(
        subtotal=subtotal, coupon_discount=coupon_discount, promotion_discount=promotion_discount,
        tax_total=tax_total, shipping_total=shipping_total, cod_fee=cod_fee, grand_total=grand_total,
        applied_promotions=applied_promotions, free_shipping=promo_free_shipping,
    )


@transaction.atomic
def create_order_from_cart(*, user, cart, address, shipping_method_id, payment_method: str, coupon_code: str = "", notes: str = ""):
    from apps.inventory.services import reserve_stock
    from apps.orders.services import generate_order_number
    from apps.shipping.models import ShippingMethod

    items = list(cart.items.select_related("variant__product").all())
    if not items:
        raise ApplicationError(ErrorCodes.CART_EMPTY, "Cart is empty.")

    totals = calculate_totals(
        cart=cart, address=address, shipping_method_id=shipping_method_id, payment_method=payment_method, coupon_code=coupon_code,
    )

    shipping_method = ShippingMethod.objects.filter(pk=shipping_method_id).first() if shipping_method_id else None

    order = Order.objects.create(
        order_number=generate_order_number(),
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

    provider_code = PAYMENT_METHOD_PROVIDER_MAP.get(payment_method)
    if not provider_code:
        raise ApplicationError(ErrorCodes.PAYMENT_PROVIDER_UNAVAILABLE, f"Payment method '{payment_method}' is not supported.")

    from apps.payments.services import create_payment_for_order

    try:
        payment = create_payment_for_order(order=order, provider_code=provider_code, method=payment_method)
    except NotImplementedError as exc:
        raise ApplicationError(ErrorCodes.PAYMENT_PROVIDER_UNAVAILABLE, str(exc)) from exc

    cart.status = CartStatus.CONVERTED
    cart.save(update_fields=["status"])

    if payment_method == PaymentMethodSnapshot.COD:
        from apps.payments.services import mark_payment_captured

        mark_payment_captured(payment)
        payment.refresh_from_db()
        order.refresh_from_db()

    return order, payment


@transaction.atomic
def confirm_order_after_payment(order: Order):
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
