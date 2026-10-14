# Shipping

Base path: `/api/v1/shipping/`

Source: `apps/shipping/{urls,views,serializers,models,services}.py`. All endpoints are public (`AllowAny`) except tracking.

## Data model — zone + method + rate, never hard-coded prices

Shipping pricing is entirely database-driven, composed from four pieces (`apps/shipping/models.py`), so changing a price or adding a governorate/carrier never requires a code change or deploy:

- **`Governorate`** — one of Egypt's 27 governorates, seeded from a fixed `EGYPT_GOVERNORATES` list (code, English name, Arabic name) in the model module. Examples: `cairo`, `giza`, `alexandria`, `qalyubia`, `sharqia`, `dakahlia`, `gharbia`, `monufia`, `beheira`, `kafr_el_sheikh`, `damietta`, `port_said`, `ismailia`, `suez`, `fayoum`, `beni_suef`, `minya`, `assiut`, `sohag`, `qena`, `luxor`, `aswan`, `red_sea`, `new_valley`, `matrouh`, `north_sinai`, `south_sinai`.
- **`City`** — belongs to a governorate.
- **`ShippingZone`** — a named group of governorates (`ManyToMany`) that share the same methods/rates, e.g. "Greater Cairo" vs "Upper Egypt".
- **`ShippingMethod`** — e.g. Standard / Express / Same day / Store pickup (`ShippingMethodType`), with `min_delivery_days`/`max_delivery_days` and an optional `carrier_code` (`bosta`, `aramex`, `self`).
- **`ShippingRate`** — the `(zone, method)` → price link. `calculation_type`:
  - `flat` — always `base_price`
  - `weight_based` — `base_price + price_per_kg × weight_kg`
  - `price_based` — resolves to `base_price`, but is only offered when the cart subtotal clears `min_order_amount`
  - `free` — always 0
  - Independent of `calculation_type`, any rate with a `free_shipping_threshold` becomes free once `subtotal >= free_shipping_threshold`.

`Address.governorate`/`Address.city` (see `docs/api/authentication.md`) reference these same tables — an address never stores its own shipping price.

## GET /api/v1/shipping/governorates/

Not paginated. Active governorates only. `GovernorateSerializer`: `id, code, name_en, name_ar`.

```json
{
  "success": true, "message": "Success",
  "data": [
    { "id": 1, "code": "cairo", "name_en": "Cairo", "name_ar": "القاهرة" },
    { "id": 2, "code": "giza", "name_en": "Giza", "name_ar": "الجيزة" }
  ],
  "errors": null
}
```

## GET /api/v1/shipping/cities/?governorate=

Not paginated. `?governorate=<code>` (e.g. `cairo`) filters to that governorate's active cities; omitted, returns all active cities. `CitySerializer`: `id, name_en, name_ar, governorate`.

## GET /api/v1/shipping/quotes/?governorate=&subtotal=&currency=&weight_kg=

Auth: none. Returns every active shipping method available for the given governorate + cart, priced. This is what the checkout UI calls to populate the shipping method picker, and what `apps.checkout.services.calculate_totals`/`create_order_from_cart` call internally by method id.

| Param | Required | Notes |
|---|---|---|
| `governorate` | yes | governorate `code`, not id |
| `subtotal` | yes | cart subtotal amount, used for `price_based`/`free_shipping_threshold` gating |
| `currency` | no | default `EGP` |
| `weight_kg` | no | total cart weight, needed for `weight_based` rates to price correctly (omitted ⇒ weight-based rates fall back to `base_price` alone) |

```
GET /api/v1/shipping/quotes/?governorate=cairo&subtotal=900.00&currency=EGP&weight_kg=1.2
```

`ShippingQuoteSerializer` (plain array in `data`, sorted by method `sort_order`):
```json
{
  "success": true, "message": "Success",
  "data": [
    { "method_id": 1, "method_name": "Standard", "method_type": "standard", "min_delivery_days": 2, "max_delivery_days": 4, "price": "45.00", "currency": "EGP", "is_free": false },
    { "method_id": 2, "method_name": "Express", "method_type": "express", "min_delivery_days": 1, "max_delivery_days": 1, "price": "90.00", "currency": "EGP", "is_free": false }
  ],
  "errors": null
}
```

Errors: `SHIPPING_UNAVAILABLE` (409, unknown/inactive governorate code).

## GET /api/v1/shipping/track/{tracking_number}/

Auth: `IsAuthenticated`, scoped to `order__user=request.user` — a customer can only track shipments on their own orders. `Shipment` is created once an order is packed and assigned a carrier tracking number.

```json
{
  "success": true, "message": "Success",
  "data": {
    "tracking_number": "BST123456789EG",
    "carrier_code": "bosta",
    "tracking_url": "https://…",
    "status": "in_transit",
    "estimated_delivery": "2026-08-18",
    "shipped_at": "2026-08-16T08:00:00Z",
    "delivered_at": null,
    "events": [
      { "status": "picked_up", "description": "Package picked up from warehouse", "location": "Cairo", "occurred_at": "2026-08-16T08:00:00Z" },
      { "status": "in_transit", "description": "In transit to Nasr City", "location": "Cairo", "occurred_at": "2026-08-16T14:00:00Z" }
    ]
  },
  "errors": null
}
```

`Shipment.status` (`ShipmentStatus`): `pending | picked_up | in_transit | out_for_delivery | delivered | failed | returned | cancelled`. `ShipmentEvent` rows are append-only tracking history, populated by carrier webhooks or manual admin updates.

Errors: `NOT_FOUND` (404, unknown tracking number or not owned by caller).

## GET /api/v1/shipping/orders/{order_number}/shipments/

Auth: `IsAuthenticated`. The shipments on one of the caller's orders, oldest first, each in the tracking shape above. Another customer's order (or an order not yet shipped) returns `[]`. This is how the storefront learns the tracking number staff entered.

## Staff (`/api/v1/admin-api/`, `IsAdminUser`, audited)

- `GET shipping/governorates/?search=` — every governorate with its zone (`zone_size`, `zone_count`) and the rates of each method. `PATCH shipping/governorates/{id}/` `{is_active}` turns delivery there on/off.
- `POST shipping/governorates/{id}/rate/` `{method, base_price, free_shipping_threshold?, is_active?, apply_to_zone?}` — sets a flat fee. Without `apply_to_zone` a governorate sharing a zone is moved into its own zone (copying its other rates), so neighbours keep their fees.
- `shipping/zones/`, `shipping/rates/` — full CRUD; `shipping/methods/` — list/create/update (no delete; deactivate instead). Money fields are non-negative.
- `POST orders/{order_number}/shipment/` `{carrier_code?, tracking_number?, tracking_url?, status?, estimated_delivery?, note?}` — creates the order's shipment or updates its latest one; status changes and notes append `ShipmentEvent`s. Returns the order detail, which now includes `shipments`.
