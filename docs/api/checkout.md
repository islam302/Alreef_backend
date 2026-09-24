# Checkout

Base path: `/api/v1/checkout/`

Source: `apps/checkout/{urls,views,serializers,services}.py`. Both endpoints require `IsAuthenticated` — checkout is not available to guests; the cart must be merged into a logged-in user's cart first (see `docs/api/cart.md`).

## Why totals are always server-computed

`CheckoutSummaryView` and `CheckoutCreateView` share the exact same `calculate_totals()` function (`apps/checkout/services.py`). The frontend never computes or sends prices/totals — it only sends *choices* (address, shipping method, payment method, coupon code); the backend is the sole source of truth for money math (spec §36), which also means the summary preview and the final order total can never disagree as long as cart contents/address/method are unchanged between the two calls.

## Request body — both endpoints

`CheckoutInputSerializer`:

| Field | Type | Required | Notes |
|---|---|---|---|
| `address_id` | int | yes | must belong to `request.user` (`apps.users.models.Address`) |
| `shipping_method_id` | int | yes | must resolve to an available `ShippingMethod` for the address's governorate (`docs/api/shipping.md`) |
| `payment_method` | string | yes | one of `card \| mobile_wallet \| instapay \| cod`. **Do not hard-code this list** — ask `GET /payments/methods/`, which returns only the methods this installation can actually complete. An unavailable method is a 400 `PAYMENT_METHOD_NOT_AVAILABLE`. |
| `coupon_code` | string | no | applied on top of/instead of any coupon already on the cart |
| `notes` | string | no | free-text order note, max 500 chars |

```json
{
  "address_id": 12,
  "shipping_method_id": 2,
  "payment_method": "cod",
  "coupon_code": "WELCOME10",
  "notes": "Please call before delivery."
}
```

## POST /api/v1/checkout/summary/

Preview-only — **no side effects** (no order, no stock reservation, no cart mutation). Safe to call repeatedly as the user changes address/shipping/payment/coupon in the UI.

Response (`CheckoutTotalsSerializer`), all money fields as decimal strings:

```json
{
  "success": true, "message": "Success",
  "data": {
    "subtotal": "900.00",
    "coupon_discount": "90.00",
    "promotion_discount": "0.00",
    "tax_total": "0.00",
    "shipping_total": "45.00",
    "cod_fee": "15.00",
    "grand_total": "870.00",
    "currency": "EGP",
    "free_shipping": false
  },
  "errors": null
}
```

Totals breakdown (`apps/checkout/services.py:calculate_totals`):
1. `subtotal` = sum of cart line totals (`unit_price_snapshot × quantity`)
2. `promotion_discount` = automatic promotions engine result (`apps.promotions.services.compute_promotions_for_items`) — may also flag `free_shipping`
3. `coupon_discount` = if `coupon_code` given, computed via `apps.coupons.services.compute_discount`; a coupon of `discount_type=free_shipping` also forces `free_shipping=true`
4. `discounted_subtotal = subtotal − coupon_discount − promotion_discount` (floored at 0)
5. `tax_total` = per line item, `line_total × EGYPT_VAT_RATE` (default 14%) **only** for items whose product `tax_category=standard`; `zero_rated`/`exempt` products contribute 0 tax
6. `shipping_total` = quote from `apps.shipping.services.get_quote_for_method` for the chosen method + address governorate, unless `free_shipping` is true (then 0)
7. `cod_fee` = flat `COD_FEE_AMOUNT` (default 15.00 EGP) only when `payment_method=cod`; raises if COD is disabled server-side
8. `grand_total = discounted_subtotal + tax_total + shipping_total + cod_fee`

Errors: `CART_EMPTY` (400), `ADDRESS_NOT_FOUND` (404), `SHIPPING_METHOD_NOT_FOUND` (404), `SHIPPING_UNAVAILABLE` (409), `INVALID_COUPON` / `COUPON_EXPIRED` / `COUPON_USAGE_LIMIT_REACHED` / `COUPON_MINIMUM_NOT_MET` / `COUPON_NOT_APPLICABLE`, `PAYMENT_PROVIDER_UNAVAILABLE` (503, COD disabled).

## POST /api/v1/checkout/

Creates the `Order`. **Requires the `Idempotency-Key` header** (see `docs/api/errors.md` for the general contract). Throttle scope `checkout` (**30/min**).

### Why Idempotency-Key is mandatory here

Placing an order has real side effects — it reserves stock, creates immutable order/line-item snapshots, and (for non-COD methods) round-trips to a payment provider. A dropped response on a flaky mobile connection must never let the client's automatic retry create a second order for the same purchase. The client should generate one UUID per checkout attempt and resend it unchanged on retry; the server replays the exact stored response for a repeated key + identical body, and returns `IDEMPOTENCY_KEY_CONFLICT` (409) if the same key is reused with a **different** body, or if the original request is still in flight.

```
POST /api/v1/checkout/
Idempotency-Key: 8f14e45f-ceea-467e-b4bd-e5b3f6b7c34d
Content-Type: application/json

{ "address_id": 12, "shipping_method_id": 2, "payment_method": "cod", "coupon_code": "WELCOME10" }
```

What happens server-side (`apps/checkout/services.py:create_order_from_cart`), inside one DB transaction:
1. Recompute totals (same `calculate_totals` as the summary endpoint) — if the cart is empty, aborts with `CART_EMPTY`.
2. Create the `Order` with immutable snapshots of the address, customer contact info, and all totals — later changes to the product catalog, addresses, or prices never retroactively affect this order.
3. Reserve stock per line item (`apps.inventory.services.reserve_stock`) and create `OrderItem` rows with their own price/name/SKU snapshots.
4. Create a `Payment` via the provider mapped from `payment_method` (see `docs/api/payments.md` for which providers are actually wired up; unsupported/unimplemented providers surface as `PAYMENT_PROVIDER_UNAVAILABLE`).
5. Mark the cart `status=converted`.
6. If `payment_method=cod`, immediately mark the payment `captured` — this also transitions the order `pending → confirmed`, consumes the stock reservation, records coupon usage, and fires order/payment confirmation emails.

Success `201` (`CheckoutResultSerializer`):

```json
{
  "success": true,
  "message": "Order created.",
  "data": {
    "order": {
      "id": 331, "order_number": "ORD-20260814-00042", "status": "confirmed", "currency": "EGP",
      "customer_name": "Sara Mostafa", "customer_email": "sara.mostafa@example.com", "customer_phone": "+201001234567",
      "shipping_address_snapshot": { "label": "home", "full_name": "Sara Mostafa", "phone": "+201001234567", "governorate": "Cairo", "city": "Nasr City", "district": "Nasr City", "street": "Makram Ebeid St.", "building": "12", "floor": "4", "apartment": "9", "landmark": "Near City Stars", "postal_code": "11371", "notes": "" },
      "shipping_method_name": "Standard", "payment_method": "cod",
      "subtotal": "900.00", "coupon_code": "WELCOME10", "coupon_discount": "90.00", "promotion_discount": "0.00",
      "loyalty_discount": "0.00", "gift_card_amount_used": "0.00", "store_credit_amount_used": "0.00",
      "tax_total": "0.00", "shipping_total": "45.00", "cod_fee": "15.00", "grand_total": "870.00",
      "notes": "Please call before delivery.", "cancel_reason": "",
      "items": [ { "id": 900, "product": 101, "variant": 501, "product_name_snapshot": "Sidr Honey", "variant_name_snapshot": "500g", "sku_snapshot": "HNY-SDR-001-500G", "unit_price": "450.00", "quantity": 2, "discount_amount": "0.00", "tax_amount": "0.00", "line_total": "900.00" } ],
      "status_history": [ { "from_status": "", "to_status": "pending", "note": "", "created_at": "2026-08-14T10:20:00Z" }, { "from_status": "pending", "to_status": "confirmed", "note": "", "created_at": "2026-08-14T10:20:01Z" } ],
      "confirmed_at": "2026-08-14T10:20:01Z", "shipped_at": null, "delivered_at": null, "cancelled_at": null,
      "created_at": "2026-08-14T10:20:00Z"
    },
    "payment": {
      "id": 210, "provider": "cod", "status": "captured",
      "redirect_url": "", "iframe_url": "", "provider_reference": ""
    }
  },
  "errors": null
}
```

For a non-COD `payment_method` whose provider is actually implemented, `payment.status` would be `pending`/`authorized` and `redirect_url`/`iframe_url` would carry the gateway's hosted checkout URL for the frontend to redirect the customer to; the client then polls `GET /api/v1/payments/{id}/status/` (see `docs/api/payments.md`). **As of this writing only `cod` completes end-to-end** — see `docs/api/payments.md` for the status of the other gateways.

Errors (in addition to the summary endpoint's list): `IDEMPOTENCY_KEY_REQUIRED` (400, header missing), `IDEMPOTENCY_KEY_CONFLICT` (409), `INSUFFICIENT_STOCK` / `NO_VALID_BATCH` (409, stock reservation failed), `PAYMENT_FAILED` (402, provider rejected payment creation), `PAYMENT_PROVIDER_UNAVAILABLE` (503, unimplemented/disabled provider — the whole order creation is rolled back).
