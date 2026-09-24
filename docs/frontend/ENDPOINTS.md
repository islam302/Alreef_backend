# Endpoint reference

Every route mounted under `/api/v1/`, read out of `config/api_v1.py` plus each
`apps/*/urls.py` and `apps/*/views.py`. Use this as the lookup table,
[`README.md`](./README.md) for the narrative explanation of each domain, and
[`types.ts`](./types.ts) for payload shapes.

## How to read the columns

- **Auth** — `public` = `AllowAny`, `user` = `IsAuthenticated`, `admin` = `IsAdminUser`,
  `session` = works logged-out but needs the Django session cookie
  (`withCredentials: true`).
- **Data shape** — what sits inside the envelope's `data` key:
  - `object` — a single resource.
  - `array` — a plain JSON array (the view sets `pagination_class = None` or overrides
    `list()`). **No `results`/`count` wrapper.**
  - `page` — the paginated shape `{ results, count, num_pages, current_page, page_size,
    next, previous }`.
  - `null` — no payload, only `message`.
- **Throttle** — the `throttle_scope` on the view. Blank means it falls back to the
  global `anon` 60/min · `user` 300/min safety net.

Every row returns the standard envelope `{ success, message, data, errors }` regardless
of shape.

---

## Auth — `/api/v1/auth/`

| Method | Path | Auth | Body | Data shape | Throttle |
|---|---|---|---|---|---|
| POST | `/auth/register/` | public | `{ email, password, first_name, last_name, phone? }` | `object` — `User` (201) | `auth` 20/min |
| POST | `/auth/login/` | public | `{ email, password }` | `object` — `{ access, refresh, user }` | `auth` 20/min |
| POST | `/auth/logout/` | user | `{ refresh }` | `null` | `auth` 20/min |
| POST | `/auth/token/refresh/` | public | `{ refresh }` | `object` — `{ access, refresh }` | `auth` 20/min |
| GET | `/auth/me/` | user | — | `object` — `Me` | — |
| PATCH | `/auth/me/` | user | partial `User` + nested `profile` | `object` — `Me` | — |
| POST | `/auth/change-password/` | user | `{ old_password, new_password }` | `null` | `auth_confirm` 10/min |
| POST | `/auth/password-reset/` | public | `{ email }` | `null` | `auth` 20/min |
| POST | `/auth/password-reset-confirm/` | public | `{ token, new_password }` | `null` | `auth_confirm` 10/min |
| POST | `/auth/verify-email/` | user | — | `null` | `auth` 20/min |
| POST | `/auth/verify-email/confirm/` | public | `{ token }` | `null` | `auth_confirm` 10/min |
| POST | `/auth/verify-phone/` | user | `{ phone? }` | `null` | `auth` 20/min |
| POST | `/auth/verify-phone/confirm/` | user | `{ code }` | `null` | `auth_confirm` 10/min |

`auth_confirm` is deliberately tighter (10/min) because those endpoints take a secret a
caller could brute-force. Debounce "resend code" buttons accordingly, and never
auto-retry a failed confirm.

## Addresses — `/api/v1/addresses/`

A plain DRF `ModelViewSet` scoped to the current user (`IsOwner`).

| Method | Path | Auth | Data shape |
|---|---|---|---|
| GET | `/addresses/` | user | `array` — `Address[]` |
| POST | `/addresses/` | user | `object` — `Address` (201) |
| GET | `/addresses/{id}/` | user | `object` |
| PUT / PATCH | `/addresses/{id}/` | user | `object` |
| DELETE | `/addresses/{id}/` | user | `null` (204) |
| POST | `/addresses/{id}/set-default/` | user | `object` |

`governorate` in the request body is a Governorate **id** (from
`/shipping/governorates/`), while `/shipping/cities/?governorate=` takes a governorate
**code**. Different identifiers on purpose — mixing them up is the most common
address-form bug.

## Catalog — `/api/v1/products/`

| Method | Path | Auth | Data shape | Throttle |
|---|---|---|---|---|
| GET | `/products/` | public | `page` — `ProductListItem[]` | `search` 60/min |
| GET | `/products/recently-viewed/` | session | `array` — `ProductListItem[]` | — |
| GET | `/products/{slug}/` | public | `object` — `ProductDetail` | — |
| GET | `/products/{slug}/related/` | public | `array` (max 12) | — |
| GET | `/search/?q=` | public | `page` — `ProductListItem[]` | `search` 60/min |

`/products/recently-viewed/` is keyed to the authenticated user *or* the session, so it
needs `withCredentials` when logged out. `GET /products/{slug}/` records a
recently-viewed entry as a side effect — prefetching detail on hover will pollute that
list, so build hover cards from the list data you already have.

`/search/` requires `q`; omitting it is a `400 VALIDATION_ERROR`, not an empty result
set. It accepts every `ProductFilter` param `/products/` does, plus `ordering=rank`.

## Taxonomy

| Method | Path | Auth | Data shape |
|---|---|---|---|
| GET | `/categories/` | public | `array` — filter with `?parent=<id>`, `?search=` |
| GET | `/categories/tree/` | public | `array` — recursive `children` |
| GET | `/categories/{slug}/` | public | `object` — includes `breadcrumbs` |
| GET | `/brands/` | public | `page` — `?search=` |
| GET | `/brands/{slug}/` | public | `object` |
| GET | `/attributes/` | public | `array` — each with nested `values` |

Note the asymmetry: categories come back as an `array`, brands as a `page`. If both feed
the same "browse by" component, normalize at the fetch layer.

## Cart — `/api/v1/cart/`

Works logged-out via the session cookie. **Every** response returns the full `Cart`, so
treat each call as a state replacement, not a patch.

| Method | Path | Auth | Body | Data shape |
|---|---|---|---|---|
| GET | `/cart/` | session | — | `object` — `Cart` |
| DELETE | `/cart/` | session | — | `object` — the now-empty `Cart` |
| POST | `/cart/items/` | session | `{ variant_id, quantity? }` | `object` — `Cart` |
| PATCH | `/cart/items/{item_id}/` | session | `{ quantity }` | `object` — `Cart` |
| DELETE | `/cart/items/{item_id}/` | session | — | `object` — `Cart` |
| POST | `/cart/coupon/` | session | `{ code }` | `object` — `Cart` |
| DELETE | `/cart/coupon/` | session | — | `object` — `Cart` |
| POST | `/cart/merge/` | user | `{ guest_cart_id? }` | `object` — `Cart` |

Two behaviours worth designing around: posting a `variant_id` already in the cart
**increments** its quantity rather than failing, and `PATCH` with `quantity: 0`
**deletes** the line rather than erroring. Both return `200` with the updated cart.

## Wishlist — `/api/v1/wishlist/`

| Method | Path | Auth | Body | Data shape |
|---|---|---|---|---|
| GET | `/wishlist/` | user | — | `array` — `WishlistItem[]` |
| POST | `/wishlist/add/` | user | `{ product_id }` | `object` (201) |
| DELETE | `/wishlist/{product_id}/` | user | — | `null` (204) |
| GET | `/wishlist/{product_id}/check/` | user | — | `object` — `{ in_wishlist }` |

Remove is keyed by **`product_id`**, not the wishlist item's own id. Add is
`get_or_create`, so double-clicking the heart is harmless.

## Reviews

| Method | Path | Auth | Body | Data shape | Throttle |
|---|---|---|---|---|---|
| GET | `/products/{product_id}/reviews/` | public | — | `page` — approved only | — |
| POST | `/reviews/` | user | `{ product_id, rating, title?, comment? }` | `object` (201) | `review` 20/min |
| POST | `/reviews/{review_id}/helpful/` | user | — | `object` | — |

The review list is keyed by the product's numeric **`id`**, while product detail is by
`slug` — you need `product.id` out of the detail response before you can fetch reviews.

## Discounts

| Method | Path | Auth | Data shape |
|---|---|---|---|
| GET | `/coupons/{code}/` | public | `object` — `Coupon` terms (preview only, does not apply) |
| GET | `/promotions/` | public | `array` — automatic promotions, no code needed |

## Shipping — `/api/v1/shipping/`

| Method | Path | Auth | Data shape |
|---|---|---|---|
| GET | `/shipping/governorates/` | public | `array` |
| GET | `/shipping/cities/?governorate=<code>` | public | `array` |
| GET | `/shipping/quotes/?governorate=<code>&subtotal=<n>` | public | `array` — `ShippingQuote[]` |
| GET | `/shipping/track/{tracking_number}/` | user | `object` — `ShipmentTracking` |

`/shipping/quotes/` also accepts `currency` and `weight_kg`. It is advisory pricing for
the method picker; `/checkout/summary/` is the authoritative total.

## Checkout — `/api/v1/checkout/`

| Method | Path | Auth | Headers | Data shape | Throttle |
|---|---|---|---|---|---|
| POST | `/checkout/summary/` | user | — | `object` — `CheckoutTotals` | — |
| POST | `/checkout/` | user | **`Idempotency-Key` required** | `object` — `{ order, payment }` (201) | `checkout` 30/min |

Both take the same `CheckoutInput` body. `summary` is a pure preview — it creates
nothing and mutates nothing, so call it on every address / method / coupon change.

## Orders — `/api/v1/orders/`

| Method | Path | Auth | Body | Data shape |
|---|---|---|---|---|
| GET | `/orders/` | user | — | `page` — `OrderListItem[]` |
| GET | `/orders/returns/` | user | — | `array` — all of the user's returns |
| GET | `/orders/{order_number}/` | user | — | `object` — `OrderDetail` |
| POST | `/orders/{order_number}/cancel/` | user | `{ reason }` | `object` |
| POST | `/orders/{order_number}/returns/` | user | `{ reason, description?, items[] }` | `object` (201) |

Lookup is by `order_number` (`ORD-YYYYMMDD-NNNN`), not the numeric `id` — even though
`id` is in the payload. Route your order pages on `order_number`.

`/orders/returns/` is declared before `/orders/{order_number}/`, so the literal path
wins and there is no collision.

## Payments — `/api/v1/payments/`

| Method | Path | Auth | Headers | Data shape |
|---|---|---|---|---|
| GET | `/payments/{id}/` | user | — | `object` — `Payment` |
| GET | `/payments/{id}/status/` | user | — | `object` — `Payment`, re-checked against the provider |
| POST | `/payments/{id}/refund/` | admin | `Idempotency-Key` required | `object` — `Refund` (201) |
| POST | `/payments/webhooks/{provider}/` | public | — | *(inbound only — gateways call this, never your app)* |

See [`PAYMENTS.md`](./PAYMENTS.md) for the full flow and which providers actually work
today.

## Loyalty, gift cards, subscriptions, notifications

| Method | Path | Auth | Body | Data shape |
|---|---|---|---|---|
| GET | `/loyalty/` | user | — | `object` — `LoyaltyAccount` |
| GET | `/loyalty/transactions/` | user | — | `page` |
| POST | `/loyalty/redeem/` | user | `{ points }` | `object` — `{ discount_value, currency }` |
| POST | `/gift-cards/check/` | user | `{ code }` | `object` — masked `GiftCard` |
| GET | `/gift-cards/store-credit/` | user | — | `object` |
| GET | `/gift-cards/store-credit/transactions/` | user | — | `page` |
| GET | `/subscriptions/` | user | — | `array` |
| POST | `/subscriptions/create/` | user | see [`README.md`](./README.md#subscriptions) | `object` (201) |
| GET | `/subscriptions/{id}/` | user | — | `object` |
| POST | `/subscriptions/{id}/pause/` · `/resume/` · `/cancel/` | user | — | `object` |
| GET | `/notifications/` | user | — | `page` |
| POST | `/notifications/{id}/read/` | user | — | `object` |
| GET / PATCH | `/notifications/preferences/` | user | marketing flags | `object` |

`/loyalty/redeem/` and `/gift-cards/check/` currently only *compute* a value — neither
is wired into `CheckoutInput`, so a redeemed amount cannot reduce an order total yet.
Don't ship checkout UI that promises it.

## Staff API — `/api/v1/admin-api/`

Everything below is `IsAdminUser` (`is_staff=True`). It exists because the
storefront routes are scoped to the requesting customer — `/orders/` returns
*your* orders — so staff could not see or manage anyone else's data through
them. Consumed by the dashboard in `Alreef_dashboard/`.

Business rules are not reimplemented here: order transitions call
`apps.orders.services.transition_status`, refunds call
`apps.payments.services.create_refund`, and stock changes go through
`apps.admin_api.services.adjust_stock`. An action the storefront considers
illegal is illegal here too.

### Dashboard

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET | `/admin-api/me/` | — | `object` — the signed-in staff account |
| GET | `/admin-api/overview/` | `?days=30` | `object` — KPIs (extends `dashboard/`) |
| GET | `/admin-api/sales-chart/` | `?days=30` | `object` — `{ series[], status_breakdown[], payment_breakdown[] }` |

`days` is clamped to 1–365.

### Orders — read, transition, annotate. Never created or deleted.

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET | `/admin-api/orders/` | `?status=&payment_method=&date_from=&date_to=&search=&ordering=` | `page` |
| GET | `/admin-api/orders/{order_number}/` | — | `object` — includes `items`, `status_history`, `payments`, `allowed_transitions` |
| POST | `/admin-api/orders/{order_number}/status/` | `{ status, note? }` | `object` |
| POST | `/admin-api/orders/{order_number}/cancel/` | `{ reason }` | `object` |
| PATCH | `/admin-api/orders/{order_number}/notes/` | `{ notes }` | `object` |

`status` and `payment_method` accept a comma-separated list. Use
`allowed_transitions` from the detail payload to build the status control —
anything else returns `400 ORDER_INVALID_TRANSITION`.

### Returns

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET | `/admin-api/returns/` | `?status=&search=` | `page` |
| GET | `/admin-api/returns/{id}/` | — | `object` |
| POST | `/admin-api/returns/{id}/decide/` | `{ approve, note? }` | `object` |
| POST | `/admin-api/returns/{id}/complete/` | — | `object` |

`complete/` is irreversible: it moves the order to RETURNED, issues a refund
for the returned lines and settles the request.

### Catalog

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET | `/admin-api/products/` | `?search=&status=&category=&brand=&published=&featured=&ordering=` | `page` |
| POST · GET · PATCH · DELETE | `/admin-api/products/` · `/{id}/` | `Product` fields | `object` |
| POST | `/admin-api/products/{id}/images/` | multipart `image`, `alt_text?`, `is_primary?` | `object` (201) |
| POST | `/admin-api/products/{id}/variants/` | `ProductVariant` fields | `object` (201) |
| PATCH · DELETE | `/admin-api/product-images/{id}/` | `{ alt_text?, is_primary?, sort_order? }` | `object` |
| GET · PATCH · DELETE | `/admin-api/variants/{id}/` | `ProductVariant` fields | `object` |
| GET · POST · PATCH · DELETE | `/admin-api/categories/` · `/{id}/` | `Category` fields | `array` (tree order, each row carries `level`) |

The category list is **not paginated** and is ordered by `(tree_id, lft)`, so
indenting on `level` reproduces the tree. Deleting a category that still has
products returns `400`. Setting an image `is_primary` clears the flag on the
product's other images. Creating a variant also creates its `InventoryItem`.

### Customers

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET | `/admin-api/customers/` | `?search=&is_active=&has_orders=&ordering=` | `page` — annotated `order_count`, `total_spent` |
| GET | `/admin-api/customers/{id}/` | — | `object` — includes `recent_orders`, `profile` |
| GET | `/admin-api/customers/{id}/orders/` | — | `page` |
| POST | `/admin-api/customers/{id}/toggle-active/` | — | `object` |

Staff accounts are excluded from this list. `ordering` accepts `order_count`
and `total_spent`.

### Inventory

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET | `/admin-api/inventory/` | `?search=&state=low\|out\|in` | `page` |
| PATCH | `/admin-api/inventory/{id}/settings/` | `{ track_inventory?, low_stock_threshold?, allow_backorder? }` | `object` |
| POST | `/admin-api/inventory/{id}/adjust/` | `{ quantity_delta, reason, notes?, batch_number?, expiration_date? }` | `object` |
| GET | `/admin-api/inventory/alerts/` | `?expiry_days=30` | `object` — `{ low_stock[], out_of_stock[], expiring_soon[], expired[] }` |
| GET | `/admin-api/inventory-movements/` | `?item=&movement_type=` | `page` |
| GET · POST · PATCH · DELETE | `/admin-api/batches/` · `/{id}/` | `Batch` fields | `object` |

`adjust/` is the only way to change stock from the dashboard. A positive
delta lands in a named batch (created if absent); a negative delta is drawn
FEFO, exactly as a sale would be, and returns `400 INSUFFICIENT_STOCK` rather
than going negative. Every adjustment writes an `InventoryMovement`.

`state` is evaluated in Python because availability depends on live batch and
reservation sums.

### Marketing

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET · POST · PATCH · DELETE | `/admin-api/coupons/` · `/{id}/` | `Coupon` fields | `object` / `page` |
| GET | `/admin-api/reviews/` | `?approved=&rating=&search=` | `page` |
| POST | `/admin-api/reviews/{id}/approve/` · `/reject/` | — | `object` |
| DELETE | `/admin-api/reviews/{id}/` | — | `null` |

A review's rating and text are read-only to staff — moderation flips
`approved`, it does not rewrite what a customer wrote.

### Payments

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET | `/admin-api/payments/` | `?status=&provider=&search=` | `page` — each row nests its `refunds` |
| GET | `/admin-api/payments/{id}/` | — | `object` |
| POST | `/admin-api/payments/{id}/refund/` | `{ amount?, reason?, destination? }` | `object` (201) |

Omit `amount` to refund in full. This is the same service the existing
`/payments/{id}/refund/` route uses.

### System

| Method | Path | Body | Data shape |
|---|---|---|---|
| GET | `/admin-api/audit-logs/` | `?action=&model_name=&date_from=&date_to=&search=` | `page` |
| GET | `/admin-api/staff/` | `?search=` | `page` |

`staff/` is read-only. Creating staff and granting permissions stays in
Django admin so password hashing and permission groups live in one place.

## Analytics — `/api/v1/admin-api/`

| Method | Path | Auth | Body | Data shape |
|---|---|---|---|---|
| POST | `/admin-api/events/track/` | public | `{ event_type, product_id?, order_id?, metadata? }` | `null` (202) |
| GET | `/admin-api/dashboard/` | admin | — | `object` |

Despite living under `admin-api/`, **`events/track/` is public** and is meant to be
called from the storefront. It is fire-and-forget: it returns `202` and processes
asynchronously, so never await it in a user-blocking path. `event_type` must be one of
`product_view`, `search`, `add_to_cart`, `remove_from_cart`, `checkout_started`,
`payment_started`, `payment_success`, `payment_failed`, `order_created`,
`order_delivered`, `refund_created`, `coupon_used`, `review_created` — anything else is
a `400 VALIDATION_ERROR`.

## Outside `/api/v1/`

| Path | What |
|---|---|
| `/api/docs/` | Swagger UI — live, always matches the deployed code |
| `/api/redoc/` | ReDoc rendering of the same schema |
| `/api/schema/` | Raw OpenAPI 3 YAML — feed this to a codegen tool |
| `/api/health/` | Health check (db, cache, storage) |
| `/admin/` | Django admin (staff only) |

When this file and `/api/schema/` disagree, the schema wins — it is generated from the
running code, this is maintained by hand.
