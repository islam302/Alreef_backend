from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.coupons.models import Coupon, CouponDiscountType, CouponUsage


def get_valid_coupon(code: str) -> Coupon:
    coupon = Coupon.objects.filter(code__iexact=code, is_active=True).first()
    if not coupon:
        raise ApplicationError(ErrorCodes.INVALID_COUPON, "Coupon code is invalid.")
    now = timezone.now()
    if coupon.start_date and coupon.start_date > now:
        raise ApplicationError(ErrorCodes.INVALID_COUPON, "Coupon is not active yet.")
    if coupon.expiration_date and coupon.expiration_date < now:
        raise ApplicationError(ErrorCodes.COUPON_EXPIRED, "Coupon has expired.")
    if coupon.usage_limit is not None and coupon.times_used >= coupon.usage_limit:
        raise ApplicationError(ErrorCodes.COUPON_USAGE_LIMIT_REACHED, "Coupon usage limit has been reached.")
    return coupon


def _check_per_user_limit(coupon: Coupon, user):
    if user and coupon.per_user_limit is not None:
        used = CouponUsage.objects.filter(coupon=coupon, user=user).count()
        if used >= coupon.per_user_limit:
            raise ApplicationError(ErrorCodes.COUPON_USAGE_LIMIT_REACHED, "You have already used this coupon the maximum number of times.")


def _applicable_items(coupon: Coupon, items):
    """items: iterable of objects with `.variant.product` and `.line_total`."""
    if not coupon.is_scoped:
        return list(items)
    product_ids = set(coupon.products.values_list("id", flat=True))
    category_ids = set(coupon.categories.values_list("id", flat=True))
    brand_ids = set(coupon.brands.values_list("id", flat=True))
    applicable = []
    for item in items:
        product = item.variant.product
        if product.id in product_ids or product.category_id in category_ids or product.brand_id in brand_ids:
            applicable.append(item)
    return applicable


def validate_coupon_for_cart(*, code: str, cart, user=None) -> Coupon:
    coupon = get_valid_coupon(code)
    _check_per_user_limit(coupon, user)

    from apps.carts.services import get_cart_subtotal

    items = list(cart.items.select_related("variant__product").all())
    if not items:
        raise ApplicationError(ErrorCodes.CART_EMPTY, "Cannot apply a coupon to an empty cart.")

    subtotal = get_cart_subtotal(cart)
    if coupon.minimum_order_amount and subtotal < coupon.minimum_order_amount:
        raise ApplicationError(
            ErrorCodes.COUPON_MINIMUM_NOT_MET,
            f"This coupon requires a minimum order of {coupon.minimum_order_amount}.",
        )

    if coupon.is_scoped and not _applicable_items(coupon, items):
        raise ApplicationError(ErrorCodes.COUPON_NOT_APPLICABLE, "This coupon does not apply to any items in your cart.")

    return coupon


def compute_discount(*, coupon: Coupon, items, subtotal: Money) -> Money:
    """Returns the discount amount (not free-shipping — that's handled by checkout's shipping step)."""
    applicable = _applicable_items(coupon, items)
    applicable_subtotal = sum((i.line_total for i in applicable), start=Money(0, subtotal.currency)) if coupon.is_scoped else subtotal

    if coupon.discount_type == CouponDiscountType.FREE_SHIPPING:
        return Money(0, subtotal.currency)

    if coupon.discount_type == CouponDiscountType.PERCENTAGE:
        discount = applicable_subtotal * (coupon.percentage_value / 100)
    elif coupon.discount_type == CouponDiscountType.FIXED_AMOUNT:
        discount = min(coupon.fixed_amount_value, applicable_subtotal)
    else:
        discount = Money(0, subtotal.currency)

    if coupon.maximum_discount_amount and discount > coupon.maximum_discount_amount:
        discount = coupon.maximum_discount_amount

    return discount


def record_usage(*, coupon: Coupon, user, order, discount_amount: Money):
    CouponUsage.objects.create(coupon=coupon, user=user, order=order, discount_amount=discount_amount)
