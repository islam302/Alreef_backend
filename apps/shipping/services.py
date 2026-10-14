from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.shipping.models import (
    RateCalculationType,
    Shipment,
    ShipmentEvent,
    ShipmentStatus,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
)


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


# --------------------------------------------------------------------------
# Staff-side changes (dashboard). The models price by zone, so a fee "for one
# governorate" is really a fee for a zone that holds only that governorate.
# --------------------------------------------------------------------------
def dedicated_zone_for(governorate) -> ShippingZone:
    """
    The zone that holds `governorate` and nothing else, made on demand.

    A governorate that shares a zone ("Rest of Egypt") is moved into a new zone
    of its own that starts with a copy of every rate it had, so changing its
    fee leaves its neighbours and its other methods exactly as they were. It is
    also taken out of any other zone: quotes are summed across every zone a
    governorate is in, and two zones would offer the same method twice.
    """
    zones = list(governorate.zones.order_by("created_at"))
    # Counted per zone, not annotated: `governorate.zones` already joins the
    # same table, so an annotated Count would only ever see this governorate.
    if len(zones) == 1 and zones[0].governorates.count() == 1:
        return zones[0]

    source = next((z for z in zones if z.is_active), zones[0] if zones else None)
    zone = ShippingZone.objects.create(name=governorate.name_ar, is_active=source.is_active if source else True)
    zone.governorates.add(governorate)
    if source is not None:
        for rate in source.rates.all():
            rate.pk = None
            rate.id = None
            rate.zone = zone
            rate.save(force_insert=True)
    for old in zones:
        old.governorates.remove(governorate)
    return zone


@transaction.atomic
def set_governorate_rate(
    *, governorate, method, base_price: Decimal, free_shipping_threshold: Decimal | None,
    is_active: bool = True, apply_to_zone: bool = False,
) -> ShippingRate:
    """
    Set what `method` costs to deliver to `governorate`.

    `apply_to_zone` changes the rate of the zone the governorate already shares
    (every governorate in it gets the new fee); otherwise only this governorate
    changes. Rates are always flat here: weight- or price-based rules stay
    editable from the zone/rate endpoints, but the dashboard's per-governorate
    screen asks for one number and should mean exactly that number.
    """
    governorate = type(governorate).objects.select_for_update().get(pk=governorate.pk)
    if apply_to_zone:
        zones = list(governorate.zones.all())
        if len(zones) != 1:
            raise ApplicationError(
                ErrorCodes.VALIDATION_ERROR,
                "This governorate is not in exactly one zone, so there is no single zone fee to change.",
            )
        zone = zones[0]
    else:
        zone = dedicated_zone_for(governorate)

    currency = "EGP"
    rate = ShippingRate.objects.filter(zone=zone, method=method).first()
    if rate is None:
        rate = ShippingRate(zone=zone, method=method)
    else:
        currency = str(rate.base_price_currency or currency)
    rate.calculation_type = RateCalculationType.FLAT
    rate.base_price = Money(base_price, currency)
    rate.free_shipping_threshold = (
        Money(free_shipping_threshold, currency) if free_shipping_threshold is not None else None
    )
    rate.is_active = is_active
    rate.save()
    return rate


def _shipping_method_for(order) -> ShippingMethod:
    """The method a shipment is booked under. Orders keep only the method's
    name (a snapshot), so match on that and fall back to the first one."""
    methods = ShippingMethod.objects.order_by("-is_active", "sort_order", "id")
    method = None
    if order.shipping_method_name:
        method = methods.filter(name=order.shipping_method_name).first()
    method = method or methods.first()
    if method is None:
        raise ApplicationError(ErrorCodes.SHIPPING_METHOD_NOT_FOUND, "No shipping method exists to book the shipment under.")
    return method


# A shipment that has reached one of these has left the warehouse.
_SHIPPED_STATUSES = {
    ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT, ShipmentStatus.OUT_FOR_DELIVERY, ShipmentStatus.DELIVERED,
}


@transaction.atomic
def save_order_shipment(*, order, data: dict) -> tuple[Shipment, dict]:
    """
    Create the order's shipment, or update the latest one, from what staff
    typed (carrier, tracking number and link, status, expected date).

    Every status change, and every note, becomes a ShipmentEvent — that table
    is what the customer's tracking timeline reads. Returns the shipment and
    the fields that actually changed, for the audit row.
    """
    from apps.orders.models import OrderStatus

    if order.status == OrderStatus.CANCELLED:
        raise ApplicationError(ErrorCodes.VALIDATION_ERROR, "A cancelled order cannot be shipped.")

    shipment = order.shipments.select_for_update().order_by("-created_at").first()
    created = shipment is None
    if created:
        shipment = Shipment(order=order, shipping_method=_shipping_method_for(order))

    previous_status = None if created else shipment.status
    changes = {}
    for field in ("carrier_code", "tracking_number", "tracking_url", "status", "estimated_delivery"):
        if field in data and (created or getattr(shipment, field) != data[field]):
            setattr(shipment, field, data[field])
            changes[field] = str(data[field]) if data[field] is not None else None

    now = timezone.now()
    if shipment.status in _SHIPPED_STATUSES and shipment.shipped_at is None:
        shipment.shipped_at = now
    if shipment.status == ShipmentStatus.DELIVERED and shipment.delivered_at is None:
        shipment.delivered_at = now
    shipment.save()

    note = (data.get("note") or "").strip()
    if created or shipment.status != previous_status or note:
        ShipmentEvent.objects.create(shipment=shipment, status=shipment.status, description=note, occurred_at=now)
        if note:
            changes["note"] = note
    return shipment, changes
