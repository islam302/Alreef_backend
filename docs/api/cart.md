# Cart

Base path: `/api/v1/cart/`

Source: `apps/carts/{urls,views,serializers,models,services}.py`.

## Guest vs. authenticated carts

Every cart endpoint is `AllowAny`. `apps.carts.services.get_or_create_cart(request)` decides which `Cart` to use:

- **Authenticated** (`request.user.is_authenticated`): looks up/creates the single `Cart` with `user=request.user, status=open`.
- **Guest**: uses the Django session (`request.session.session_key`, saving the session if it doesn't have one yet) and looks up/creates the `Cart` with `session_key=<key>, user=None, status=open`.

`Cart.id` is a UUID (`UUIDPrimaryKeyModel`) — the frontend should hold onto the guest cart's `id` (returned in every cart response) so it can be passed to the merge endpoint after login.

`Cart.status`: `open | merged | converted | abandoned`. A cart moves to `converted` the moment `POST /checkout/` succeeds (see `docs/api/checkout.md`), and to `abandoned` via the `process-abandoned-carts` Celery beat task after `ABANDONED_CART_HOURS` (default 6h) of inactivity with items still in it.

## Cart response shape

`CartSerializer`:
```json
{
  "id": "b3b1e6b0-9e2d-4f7a-9b3a-2b6a2f7c1d10",
  "currency": "EGP",
  "coupon_code": "WELCOME10",
  "items": [
    {
      "id": 87,
      "variant": { "id": 501, "sku": "HNY-SDR-001-500G", "barcode": "", "name_suffix": "500g", "price": "450.00", "compare_at_price": "500.00", "weight_grams": 500, "is_default": true, "attributes": [], "images": [], "stock": { "available_quantity": 42, "is_low_stock": false, "is_out_of_stock": false } },
      "product_name": "Sidr Honey",
      "quantity": 2,
      "unit_price_snapshot": "450.00",
      "line_total": "900.00"
    }
  ],
  "subtotal": "900.00",
  "item_count": 2,
  "created_at": "2026-08-14T09:00:00Z",
  "updated_at": "2026-08-14T09:10:00Z"
}
```

`unit_price_snapshot` is captured at the moment the item was added/updated and is refreshed to the variant's current price on every add/update call — it is **not** locked in until checkout creates the order (order line items get their own immutable snapshot then).

## GET /api/v1/cart/

Returns (creating if necessary) the caller's current open cart.

## DELETE /api/v1/cart/

Clears all items and resets `coupon_code`. Returns the now-empty cart. Message: `"Cart cleared."`

## POST /api/v1/cart/items/

Add an item. Request (`AddCartItemSerializer`):
```json
{ "variant_id": 501, "quantity": 2 }
```
`quantity` defaults to 1, minimum 1. If the variant is already in the cart, quantities are **summed** (not replaced) and the price snapshot is refreshed. Message: `"Item added to cart."`

Errors: `VARIANT_NOT_FOUND` (404, variant missing or `is_active=False`).

## PATCH /api/v1/cart/items/{item_id}/

Set an absolute quantity. Request (`UpdateCartItemSerializer`): `{ "quantity": 3 }` (`min_value=0`). Setting `quantity=0` **deletes** the line item. Message: `"Cart updated."`

Errors: `CART_ITEM_NOT_FOUND` (404, item not in this cart).

## DELETE /api/v1/cart/items/{item_id}/

Removes the line item outright. Message: `"Item removed."` Errors: `CART_ITEM_NOT_FOUND` (404).

## POST /api/v1/cart/coupon/

Applies a coupon code to the cart (validated but not yet "spent" — validity/usage limits are re-checked and recorded at order confirmation). Request: `{ "code": "WELCOME10" }`. Delegates to `apps.coupons.services.validate_coupon_for_cart`.

Errors: `INVALID_COUPON`, `COUPON_EXPIRED`, `COUPON_USAGE_LIMIT_REACHED`, `COUPON_MINIMUM_NOT_MET`, `COUPON_NOT_APPLICABLE` (all 400/409 — see `docs/api/errors.md`).

## DELETE /api/v1/cart/coupon/

Clears `coupon_code` from the cart. Always succeeds.

## POST /api/v1/cart/merge/

Auth: **`IsAuthenticated`** (the only cart endpoint that requires login) — call this immediately after login/register if the client was carrying a guest cart.

Request: `{ "guest_cart_id": "b3b1e6b0-9e2d-4f7a-9b3a-2b6a2f7c1d10" }`

Merge rule (`apps.carts.services.merge_guest_cart_into_user`, spec §24): the user's existing open cart is the merge target.
- If a variant exists in **both** carts, quantities are **summed** and the price snapshot refreshed to the variant's current price.
- If a variant exists only in the guest cart, its line item is copied over as-is.
- If the user's cart has no coupon applied but the guest cart did, the guest cart's `coupon_code` carries over (existing user coupon always wins, never overwritten).
- The guest cart is marked `status=merged` and emptied.

If `guest_cart_id` is omitted, or refers to a cart that's missing/not open/already owned by a user, the endpoint is a no-op that just returns the caller's own cart (no error is raised).

Success `200`: merged `CartSerializer`, message `"Carts merged."` (or plain `CartSerializer` with default `"Success"` message on the no-op path).
