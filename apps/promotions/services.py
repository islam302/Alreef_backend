from django.db.models import Q
from django.utils import timezone
from djmoney.money import Money

from apps.promotions.models import Promotion, PromotionType


def get_active_promotions():
    now = timezone.now()
    return (
        Promotion.objects.filter(is_active=True)
        .filter(Q(start_date__isnull=True) | Q(start_date__lte=now))
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=now))
        .prefetch_related("products", "categories", "brands")
    )


def _matches_scope(promotion: Promotion, product) -> bool:
    if not promotion.is_scoped:
        return True
    return (
        promotion.products.filter(pk=product.pk).exists()
        or promotion.categories.filter(pk=product.category_id).exists()
        or promotion.brands.filter(pk=product.brand_id).exists()
    )


def compute_promotions_for_items(items, currency: str = "EGP"):
    """
    items: iterable with `.variant.product`, `.quantity`, `.unit_price_snapshot` / `.line_total`.
    Returns (total_discount: Money, applied: list[{"promotion": Promotion, "discount": Money}], free_shipping: bool)
    Non-stackable promotions: only the highest-priority match per matched
    item scope is applied; stackable promotions always add on top.
    """
    promotions = list(get_active_promotions())
    total_discount = Money(0, currency)
    applied = []
    free_shipping = False
    non_stackable_applied = False

    for promotion in promotions:
        if not promotion.stackable and non_stackable_applied:
            continue

        matching_items = [i for i in items if _matches_scope(promotion, i.variant.product)]
        if not matching_items:
            continue

        discount = _discount_for_promotion(promotion, matching_items, currency)
        if promotion.promotion_type == PromotionType.FREE_SHIPPING:
            free_shipping = True

        if discount.amount > 0 or promotion.promotion_type == PromotionType.FREE_SHIPPING:
            applied.append({"promotion": promotion, "discount": discount})
            total_discount += discount
            if not promotion.stackable:
                non_stackable_applied = True

    return total_discount, applied, free_shipping


def _discount_for_promotion(promotion: Promotion, items, currency: str) -> Money:
    subtotal = sum((i.line_total for i in items), start=Money(0, currency))

    if promotion.promotion_type == PromotionType.PERCENTAGE:
        return subtotal * (promotion.percentage_value / 100)

    if promotion.promotion_type == PromotionType.FIXED_AMOUNT:
        return min(promotion.fixed_amount_value, subtotal)

    if promotion.promotion_type == PromotionType.BUY_X_GET_Y:
        return _buy_x_get_y_discount(promotion, items, currency)

    return Money(0, currency)


def _buy_x_get_y_discount(promotion: Promotion, items, currency: str) -> Money:
    x, y = promotion.buy_quantity or 0, promotion.get_quantity or 0
    if x <= 0 or y <= 0:
        return Money(0, currency)

    total_qty = sum(i.quantity for i in items)
    cycle = x + y
    free_units = (total_qty // cycle) * y
    if free_units <= 0:
        return Money(0, currency)

    # Discount the cheapest units first (customer-favorable, standard BOGO behavior).
    unit_prices = sorted(
        (i.unit_price_snapshot for i in items for _ in range(i.quantity)),
        key=lambda m: m.amount,
    )
    discounted_units = unit_prices[:free_units]
    total = sum(discounted_units, start=Money(0, currency)) if discounted_units else Money(0, currency)
    return total * (promotion.get_discount_percent / 100)
