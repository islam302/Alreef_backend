# Shipping

Files: `apps/shipping/models.py`, `apps/shipping/services.py`,
`apps/shipping/providers/` (`base.py`, `manual.py`, `bosta.py`).

## Model shape

```
Governorate 1──* City
Governorate *──* ShippingZone   (M2M: a zone groups governorates)
ShippingZone 1──* ShippingRate *──1 ShippingMethod
Order 1──* Shipment *──1 ShippingMethod
Shipment 1──* ShipmentEvent
```

- **`Governorate`** — database-driven, not hard-coded into logic
  (`apps/shipping/models.py` docstring: "spec §45. Not hard-coded prices
  here"). `code` (slug, unique), `name_en`/`name_ar`, `is_active`,
  `sort_order`.
- **`City`** — belongs to a `Governorate`; `unique_together (governorate, name_en)`.
- **`ShippingZone`** — a named group of governorates (M2M) that share rates,
  e.g. "Greater Cairo" vs. "Upper Egypt" vs. "Remote".
- **`ShippingMethod`** — e.g. Standard/Express/Same-day/Store-pickup
  (`ShippingMethodType` choices), with `carrier_code` (`bosta`/`aramex`/`self`),
  `min_delivery_days`/`max_delivery_days`.
- **`ShippingRate`** — the zone × method → price matrix. `calculation_type`
  is one of `flat`/`weight_based`/`price_based`/`free`;
  `free_shipping_threshold` overrides any computed price once the order
  subtotal meets it; `min_order_amount` can gate a rate off entirely below a
  threshold. `unique_together (zone, method)`.
- **`Shipment`** — created once an order is packed; `order` FK is
  `PROTECT`. Tracks `carrier_code`, `tracking_number`/`tracking_url`,
  `status` (`ShipmentStatus`: pending → picked_up → in_transit →
  out_for_delivery → delivered / failed / returned / cancelled), and a raw
  `provider_payload` JSON field for whatever the carrier's API returns.
- **`ShipmentEvent`** — append-only tracking history, populated by carrier
  webhooks or manual staff updates; ordered chronologically by `occurred_at`.

## Egyptian governorate seed data

`apps/shipping/models.py` defines `EGYPT_GOVERNORATES`, a list of all 27
Egyptian governorates as `(code, name_en, name_ar)` tuples — Cairo, Giza,
Alexandria, Qalyubia, Sharqia, Dakahlia, Gharbia, Monufia, Beheira, Kafr El
Sheikh, Damietta, Port Said, Ismailia, Suez, Fayoum, Beni Suef, Minya,
Assiut, Sohag, Qena, Luxor, Aswan, Red Sea, New Valley, Matrouh, North
Sinai, South Sinai. This is the canonical seed list used to populate the
`Governorate` table (via a data migration or management command / fixture),
so addresses (`apps.users.models.Address.governorate`) always reference one
of these 27 real, correctly-spelled (English + Arabic) governorates rather
than free text.

## Rate calculation

`apps.shipping.services.get_available_quotes(governorate, subtotal, weight_kg=None)`:

1. Filters `ShippingRate` to rows whose zone is active, contains the given
   governorate, and whose method is active.
2. Skips any rate whose `min_order_amount` exceeds the order subtotal.
3. Prices each remaining rate via `_price_for_rate`:
   - if `free_shipping_threshold` is set and `subtotal >= threshold` → `Money(0, ...)`
   - `calculation_type == FREE` → `Money(0, ...)`
   - `calculation_type == WEIGHT_BASED` (and `weight_kg` provided) →
     `base_price + price_per_kg * weight_kg`
   - `FLAT` and `PRICE_BASED` both resolve to the flat `base_price`
     (`PRICE_BASED` gating already happened via `min_order_amount` above)
4. Returns quotes sorted by the method's `sort_order`.

`get_quote_for_method(governorate, method_id, subtotal, weight_kg=None)` is
the single-method variant used by checkout (`apps/checkout/services.py`
calls this inside `calculate_totals`) — it raises
`ErrorCodes.SHIPPING_METHOD_NOT_FOUND` if the requested method isn't
available for that governorate/order combination (e.g. same-day delivery
offered only in Cairo/Giza).

## Provider abstraction

`apps.shipping.providers.base.ShippingProvider` (ABC):

```python
class ShippingProvider(ABC):
    code: str = "base"

    def create_shipment(self, *, shipment, order) -> CreateShipmentResult: ...
    def cancel_shipment(self, *, shipment) -> bool: ...
    def get_tracking(self, *, shipment) -> list[TrackingUpdate]: ...
    def verify_webhook(self, *, headers: dict, body: bytes) -> bool:
        return True  # override for carriers that sign payloads
```

Docstring: "spec §46 ... New carriers (Aramex, Bosta, Mylerz, ShipBlu, DHL,
...) plug in by implementing `ShippingProvider` without touching
Order/Shipment logic. Only implement a concrete adapter once you have
verified official API docs and sandbox credentials for that carrier."

### Manual (self-fulfilled) — implemented, default

`apps.shipping.providers.manual.ManualShippingProvider` (`code = "manual"`)
does **not** call any external API — `create_shipment` returns success
immediately with an empty tracking number, and staff set tracking manually
via the admin. Docstring: "This is the default until a carrier integration
is configured with real credentials." This is the only shipping provider
that is fully functional today.

### Bosta — skeleton, not wired

`apps.shipping.providers.bosta.BostaShippingProvider` (`code = "bosta"`)
follows the same pattern as the payment gateway skeletons: `__init__` checks
`settings.SHIPPING_PROVIDERS["BOSTA"]["ENABLED"]` (default `False`,
`BOSTA_ENABLED` env var) and raises `NotImplementedError` if disabled; every
method (`create_shipment`, `cancel_shipment`, `get_tracking`) raises
`NotImplementedError` regardless, since the real Bosta API request/response
mapping has not been implemented against verified documentation. Docstring:
"this adapter implements the provider interface but does NOT fabricate
undocumented endpoints. Wire the real request/response mapping once you have
Bosta's official API reference and sandbox credentials."

`config/settings/base.py` also declares an `ARAMEX` config block
(`SHIPPING_PROVIDERS["ARAMEX"]`: `ENABLED`/`USERNAME`/`PASSWORD`/
`ACCOUNT_NUMBER`) but there is currently no `apps/shipping/providers/aramex.py`
adapter file — only the settings placeholder exists.

## Status summary

| Provider | Status |
|---|---|
| Manual (self-fulfilled) | **Implemented.** No external API; staff-driven tracking updates. |
| Bosta | **Interface-complete skeleton only** — `NotImplementedError` until real credentials + verified API docs are wired in. |
| Aramex | **Config placeholder only** (`SHIPPING_PROVIDERS["ARAMEX"]` in settings) — no adapter module exists yet. |
