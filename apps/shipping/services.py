from dataclasses import dataclass
from decimal import Decimal

from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.shipping.models import RateCalculationType, ShippingMethod, ShippingRate


@dataclass
class ShippingQuote:
    method: ShippingMethod
    rate: ShippingRate
    price: Money
    is_free: bool


def get_available_quotes(*, governorate, subtotal: Money, weight_kg: Decimal | None = None) -> list[ShippingQuote]:
    """Returns every active shipping method available for a governorate + order, priced."""
    if governorate is None:
        raise ApplicationError(ErrorCodes.SHIPPING_UNAVAILABLE, "No governorate provided for shipping calculation.")

    rates = (
        ShippingRate.objects.filter(is_active=True, zone__is_active=True, zone__governorates=governorate, method__is_active=True)
        .select_related("method", "zone")
    )
    quotes = []
    for rate in rates:
        if rate.min_order_amount and subtotal < rate.min_order_amount:
            continue
        price = _price_for_rate(rate, subtotal=subtotal, weight_kg=weight_kg)
        is_free = price.amount == 0
        quotes.append(ShippingQuote(method=rate.method, rate=rate, price=price, is_free=is_free))
    return sorted(quotes, key=lambda q: q.method.sort_order)


def get_quote_for_method(*, governorate, method_id, subtotal: Money, weight_kg: Decimal | None = None) -> ShippingQuote:
    quotes = get_available_quotes(governorate=governorate, subtotal=subtotal, weight_kg=weight_kg)
    for quote in quotes:
        if quote.method.id == method_id:
            return quote
    raise ApplicationError(ErrorCodes.SHIPPING_METHOD_NOT_FOUND, "Selected shipping method is not available for this address.")


def _price_for_rate(rate: ShippingRate, *, subtotal: Money, weight_kg: Decimal | None) -> Money:
    currency = rate.base_price_currency

    if rate.free_shipping_threshold and subtotal >= rate.free_shipping_threshold:
        return Money(0, currency)

    if rate.calculation_type == RateCalculationType.FREE:
        return Money(0, currency)

    if rate.calculation_type == RateCalculationType.WEIGHT_BASED and weight_kg:
        return rate.base_price + (rate.price_per_kg * weight_kg)

    # FLAT and PRICE_BASED both resolve to base_price; PRICE_BASED gating
    # already happened via min_order_amount above.
    return rate.base_price
