"""
The shop's default delivery fees: Standard 75 EGP, Express 100 EGP, offered to
every governorate.

Applied by migration 0002 on a running shop and by `seed_data` on a fresh one.
After that, fees are edited per governorate in the dashboard; this module is
not consulted at checkout.
"""

from decimal import Decimal

DEFAULT_FEES = {
    "standard": Decimal("75.00"),
    "express": Decimal("100.00"),
}

#: Created only when the shop has no method of that kind yet.
METHOD_DEFAULTS = {
    "standard": {"name": "Standard Delivery", "min_delivery_days": 2, "max_delivery_days": 4, "sort_order": 0},
    "express": {"name": "Express Delivery", "min_delivery_days": 1, "max_delivery_days": 2, "sort_order": 1},
}


def apply_default_fees(Governorate, ShippingZone, ShippingMethod, ShippingRate):
    """
    Make every active governorate offer Standard at 75 and Express at 100.

    Every zone gets a flat, active rate for both methods at those prices; a
    free-shipping threshold a zone already has is kept. A governorate in no
    zone is put in one, so none is left without delivery. Takes the models as
    arguments so a migration can pass its historical ones.
    """
    methods = {}
    for kind, defaults in METHOD_DEFAULTS.items():
        method = ShippingMethod.objects.filter(method_type=kind).order_by("-is_active", "sort_order", "id").first()
        if method is None:
            method = ShippingMethod.objects.create(method_type=kind, **defaults)
        elif not method.is_active:
            method.is_active = True
            method.save(update_fields=["is_active"])
        methods[kind] = method

    unzoned = Governorate.objects.filter(is_active=True, zones__isnull=True)
    if unzoned.exists():
        zone, _ = ShippingZone.objects.get_or_create(name="Rest of Egypt", defaults={"is_active": True})
        zone.governorates.add(*unzoned)

    changed = 0
    for zone in ShippingZone.objects.filter(governorates__isnull=False).distinct():
        if not zone.is_active:
            zone.is_active = True
            zone.save(update_fields=["is_active"])
        for kind, method in methods.items():
            rate = ShippingRate.objects.filter(zone=zone, method=method).first()
            if rate is None:
                rate = ShippingRate(zone=zone, method=method)
            rate.calculation_type = "flat"
            rate.base_price = DEFAULT_FEES[kind]
            rate.base_price_currency = "EGP"
            rate.min_order_amount = None
            rate.is_active = True
            rate.save()
            changed += 1
    return changed
