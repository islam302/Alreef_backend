# Coupons

Coupons are customer-entered codes applied to the cart before checkout.
Automatic, code-less discounts are **Promotions** (`GET /api/v1/promotions/`),
a separate concept — see the promotions section of `docs/api/products.md`
or `apps/promotions/`.

## Validate a coupon (preview) — `GET /api/v1/coupons/{code}/`

Public, unauthenticated. Lets the frontend show a coupon's terms before the
customer applies it.

```json
{
  "success": true,
  "message": "Success",
  "data": {
    "code": "WELCOME10",
    "description": "10% off your first order",
    "discount_type": "percentage",
    "percentage_value": "10.00",
    "fixed_amount_value": null,
    "minimum_order_amount": "200.00",
    "maximum_discount_amount": null,
    "expiration_date": null
  },
  "errors": null
}
```

Errors: `INVALID_COUPON` (404-equivalent, bad/unknown code),
`COUPON_EXPIRED`, `COUPON_USAGE_LIMIT_REACHED`.

## Apply to cart — `POST /api/v1/cart/coupon/`

```json
{ "code": "WELCOME10" }
```

Validates the coupon against the **current cart contents** — this differs
from the preview endpoint above in that it also checks:
- `COUPON_MINIMUM_NOT_MET` — cart subtotal below `minimum_order_amount`.
- `COUPON_NOT_APPLICABLE` — coupon is scoped to specific products/categories/
  brands (see `Coupon.products`/`.categories`/`.brands`) and none of the
  cart's items match.
- Per-user usage limit (`Coupon.per_user_limit`, default 1) — checked again,
  authoritatively, at checkout — the cart-level check is a convenience, not
  the final word.

Remove with `DELETE /api/v1/cart/coupon/` (no body).

## Discount types

| `discount_type` | Behavior |
|---|---|
| `percentage` | `percentage_value`% off the applicable subtotal, capped by `maximum_discount_amount` if set |
| `fixed_amount` | `fixed_amount_value` EGP off (never more than the applicable subtotal) |
| `free_shipping` | Shipping cost is zeroed at checkout; no product discount |

## Checkout is authoritative

The coupon code travels with the cart into `POST /checkout/summary/` and
`POST /checkout/` — the **backend recomputes the discount from scratch**
there; a coupon that expired or hit its usage limit between "apply to cart"
and "checkout" is rejected at checkout time. The frontend must never
compute or display a discount value it invented itself — always show what
the API returned.
