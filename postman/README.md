# Postman Collection — Alreef E-Commerce API

## Import
1. Import `Egypt-Ecommerce.postman_environment.json` (Postman → Environments → Import).
2. Import `Egypt-Ecommerce.postman_collection.json` (Postman → Collections → Import).
3. Select the **Alreef Egypt E-Commerce - Local** environment in the top-right environment picker.
4. Set `base_url` if your server isn't at `http://localhost:8000`.

## Recommended run order (variables auto-populate via test scripts)
1. **01 Auth → Login** — saves `access_token`, `refresh_token`, `user_id`.
2. **05 Products → List Products** — saves `product_id`.
3. **02 Users → Create Address** — saves `address_id`.
4. **07 Cart → Add Item to Cart** — saves `cart_id`, `cart_item_id`.
5. **13 Checkout → Checkout Summary** then **Create Checkout (Order)** — saves `order_number`, `payment_id`.
6. **15 Orders**, **14 Payments**, **16 Returns** now have real IDs to work against.

Every request carries a `success`-envelope test assertion. Requests that require
authentication use the collection-level Bearer token bound to `{{access_token}}`
— re-run **Login** whenever it expires (15 minutes) or use **Refresh Token**.

`POST /checkout/` and `POST /payments/{id}/refund/` require an `Idempotency-Key`
header — the collection generates one automatically via Postman's `{{$guid}}`
dynamic variable on each send, so re-sending the exact same request body with a
*new* run creates a new resource as expected; re-sending the identical request
(same key) returns the original result instead of duplicating it.

## Folders
`01 Auth · 02 Users · 03 Categories · 04 Brands · 05 Products · 06 Search ·
07 Cart · 08 Wishlist · 09 Addresses · 10 Coupons · 11 Promotions · 12 Shipping ·
13 Checkout · 14 Payments · 15 Orders · 16 Returns · 17 Reviews · 18 Loyalty ·
19 Gift Cards · 20 Subscriptions · 21 Notifications · 22 Admin · 23 Webhooks ·
24 Health`

## Keeping this in sync with the OpenAPI schema
The backend's OpenAPI 3 schema is served at `GET /api/schema/` and is the
source of truth (see `docs/api/`). If you change an endpoint, Postman also
supports **File → Import → Link** against that URL to regenerate a collection
skeleton, which you can then merge with the hand-authored tests/scripts here.
