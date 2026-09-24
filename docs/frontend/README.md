# Alreef API — Frontend Integration Guide

This is the integration guide for a React/Next.js/TypeScript frontend talking to the
Alreef Django REST Framework backend. Everything here was read directly out of the
backend source (`apps/*/urls.py`, `apps/*/serializers.py`, `apps/core/*`) — endpoint
paths, field names and error codes are accurate as of this writing. If something looks
off, the source of truth is always the Python code, not this file.

## The rest of this folder

Start wherever your question is:

| Document | Read it when |
|---|---|
| [`SETUP.md`](./SETUP.md) | You need the API running on localhost, seeded, with test accounts. |
| [`QUICKSTART.md`](./QUICKSTART.md) | You want a step-by-step walkthrough — login → browse → cart → checkout → COD order — with real request bodies. |
| **This file** | You want the narrative explanation of a domain. |
| [`ENDPOINTS.md`](./ENDPOINTS.md) | You need the one-line answer: what's the path, is it paginated, does it need auth. |
| [`ERROR-HANDLING.md`](./ERROR-HANDLING.md) | You're writing the auth interceptor, a form, or the checkout submit handler. **Read the 401 section before the interceptor.** |
| [`STATES.md`](./STATES.md) | You need to know which buttons to show for an order, or what a status value means. |
| [`PAYMENTS.md`](./PAYMENTS.md) | You're building checkout or a payment return page. Says which methods actually work today. |
| [`DATA-FETCHING.md`](./DATA-FETCHING.md) | You're setting up React Query / SSR and want cache keys and invalidation worked out. |
| [`I18N.md`](./I18N.md) | You're doing Arabic, RTL, money or date formatting. |
| [`types.ts`](./types.ts) | You want the TypeScript interface for a payload. |
| [`../../frontend-sdk/`](../../frontend-sdk/) | You'd rather use a ready-made client than write fetch calls. |

## Base URL

All endpoints are mounted under:

```
/api/v1/
```

e.g. `https://your-domain.com/api/v1/products/`. For local development this is
typically `http://localhost:8000/api/v1/`.

## The response envelope

**Every** response — success or error — is wrapped the same way
(`apps/core/response.py`):

```ts
{
  success: boolean;
  message: string;
  data: T | null;
  errors: { code: string; fields: Record<string, string[] | string> } | null;
}
```

- On success: `success: true`, `data` holds the payload, `errors` is `null`.
- On failure: `success: false`, `data` is `null`, `errors.code` is a machine-readable
  code from the table below, and `errors.fields` holds field-level validation errors
  (DRF's standard `{ field: ["message"] }` shape) when applicable — usually empty
  (`{}`) for single business-rule errors.
- The top-level `message` is always a human-readable string you can show directly, but
  prefer switching on `errors.code` for anything you need to branch logic on (message
  text is not guaranteed to stay stable, especially once i18n is added).

**Always unwrap `data`** before using a response — never assume the top-level object
*is* the resource.

### List endpoints (pagination)

Endpoints backed by `StandardResultsPagination` (`apps/core/pagination.py`) return a
different `data` shape:

```ts
{
  results: T[];
  count: number;
  num_pages: number;
  current_page: number;
  page_size: number;   // default 20, max 100
  next: string | null;    // full URL to the next page
  previous: string | null;
}
```

Query with `?page=2&page_size=50`. Not every list endpoint paginates — several
(`categories/`, `attributes/`, `promotions/`, `wishlist/`, `shipping/governorates/`,
`shipping/cities/`, `subscriptions/`) return a plain array in `data` because they call
`pagination_class = None` or return everything at once. Check the per-domain sections
below.

## Error codes

`errors.code` is always one of `apps/core/error_codes.py`'s `ErrorCodes` values. Full
list (see `ErrorCode` in `types.ts`):

| Code | HTTP | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Field-level validation failed — check `errors.fields`. |
| `NOT_FOUND` | 404 | Generic not-found (DRF 404 / Http404). |
| `PERMISSION_DENIED` | 403 | Authenticated but not allowed. |
| `THROTTLED` | 429 | Rate limit hit — see throttle scopes below. |
| `INTERNAL_ERROR` | 500 | Unhandled server exception. |
| `METHOD_NOT_ALLOWED` | 405 | Wrong HTTP verb. |
| `IDEMPOTENCY_KEY_CONFLICT` | 409 | Same `Idempotency-Key` reused with a different body, or a duplicate is mid-flight. |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | Missing `Idempotency-Key` header on an endpoint that requires it. |
| `AUTH_INVALID_CREDENTIALS` | 401 | Bad email/password. |
| `AUTH_TOKEN_EXPIRED` | 401 | Access or refresh token expired. |
| `AUTH_TOKEN_INVALID` | 401 | Malformed/blacklisted token. |
| `AUTH_ACCOUNT_DISABLED` | 403 | `user.is_active=False`. |
| `AUTH_EMAIL_NOT_VERIFIED` | 403 | Reserved for flows that require verified email. |
| `AUTH_EMAIL_ALREADY_EXISTS` | 409 | Registration with a taken email. |
| `AUTH_PHONE_ALREADY_EXISTS` | 409 | Registration with a taken phone. |
| `AUTH_OTP_INVALID` | 400 | Wrong phone verification code. |
| `AUTH_OTP_EXPIRED` | 400 | Phone verification code expired. |
| `PRODUCT_NOT_FOUND` / `PRODUCT_NOT_PUBLISHED` | 404 | Product missing or not live. |
| `PRODUCT_OUT_OF_STOCK` | 409 | — |
| `VARIANT_NOT_FOUND` | 404 | Bad `variant_id`. |
| `INSUFFICIENT_STOCK` | 409 | Not enough stock to reserve. |
| `RESERVATION_EXPIRED` | 409 | Stock reservation timed out. |
| `NO_VALID_BATCH` | 409 | Inventory batch/FEFO issue. |
| `CART_EMPTY` | 400 | Checkout attempted with no items. |
| `CART_ITEM_NOT_FOUND` | 404 | Bad cart item id. |
| `CHECKOUT_SESSION_EXPIRED` | 410 | — |
| `ADDRESS_INVALID` | 400 | — |
| `ADDRESS_NOT_FOUND` | 404 | `address_id` doesn't belong to the user. |
| `SHIPPING_UNAVAILABLE` | 409 | Unknown/inactive governorate or no rate configured. |
| `SHIPPING_METHOD_NOT_FOUND` | 404 | Bad `shipping_method_id`. |
| `INVALID_COUPON` / `COUPON_EXPIRED` / `COUPON_USAGE_LIMIT_REACHED` / `COUPON_MINIMUM_NOT_MET` / `COUPON_NOT_APPLICABLE` | 400/409 | Coupon validation failures — show inline on the coupon field. |
| `ORDER_NOT_FOUND` | 404 | — |
| `ORDER_CANNOT_CANCEL` | 409 | Order status doesn't allow cancellation (see state machine below). |
| `ORDER_INVALID_TRANSITION` | 409 | — |
| `PAYMENT_FAILED` | 402 | — |
| `PAYMENT_EXPIRED` | 410 | — |
| `PAYMENT_AMOUNT_MISMATCH` | 409 | — |
| `PAYMENT_PROVIDER_UNAVAILABLE` | 503 | e.g. COD disabled in settings. |
| `PAYMENT_ALREADY_CAPTURED` | 409 | — |
| `PAYMENT_WEBHOOK_INVALID_SIGNATURE` | 400 | Server-side only, never seen by the frontend. |
| `REFUND_EXCEEDS_CAPTURED_AMOUNT` | 409 | Admin-only endpoint. |
| `GIFT_CARD_INVALID` / `GIFT_CARD_EXPIRED` / `GIFT_CARD_INSUFFICIENT_BALANCE` | 400/409 | — |
| `LOYALTY_INSUFFICIENT_POINTS` | 409 | — |
| `REVIEW_NOT_ELIGIBLE` | 403 | e.g. review without a verified purchase, depending on business rules. |
| `REVIEW_ALREADY_EXISTS` | 409 | One review per (product, user, order_item). |

Build a `code -> user-facing message` map on the frontend rather than displaying raw
`message` strings for anything user-critical (checkout, payment, auth).

### Throttle scopes

`apps/core/settings/base.py` `DEFAULT_THROTTLE_RATES`: `auth` 20/min, `checkout`
30/min, `payment_webhook` 120/min, `search` 60/min (applies to product list/search too).
Expect occasional `429 THROTTLED` under load/tests and back off.

## Authentication

### Register

`POST /api/v1/auth/register/` (`AllowAny`)

```json
{ "email": "a@b.com", "phone": "+201234567890", "first_name": "Amr", "last_name": "Ali", "password": "..." }
```
`phone` is optional. Returns the created `User` (201) and triggers an email
verification email server-side — registration does **not** log the user in or return
tokens. You must call `/auth/login/` afterwards.

### Login

`POST /api/v1/auth/login/` (`AllowAny`) — body: `{ "email": "...", "password": "..." }`.
Returns `LoginResult` = `{ access, refresh, user }`.

### Token storage strategy

The backend uses SimpleJWT with:
- Access token lifetime: 15 minutes
- Refresh token lifetime: 14 days
- `ROTATE_REFRESH_TOKENS=True`, `BLACKLIST_AFTER_ROTATION=True` — **every** refresh
  call invalidates the old refresh token and issues a new one. You must persist the
  new `refresh` value every time you call `/auth/token/refresh/`, or the user will be
  logged out on the next refresh attempt.

Two reasonable strategies, in order of preference:

1. **httpOnly cookie (most secure).** Have your Next.js server (API route / route
   handler) proxy auth calls and set the access+refresh tokens as httpOnly, `Secure`,
   `SameSite=Lax` cookies. The browser never sees the raw JWT, which eliminates XSS
   token theft. This requires a thin backend-for-frontend layer since the Django API
   itself issues tokens in the JSON body, not as cookies.
2. **In-memory access token + localStorage refresh token (pragmatic, common in
   practice).** Keep the access token in a JS variable/React context only (never
   persisted), and store the refresh token in `localStorage` so a page reload doesn't
   force a full re-login. On app boot, if a refresh token exists, silently call
   `/auth/token/refresh/` to mint a new access token before rendering anything
   auth-gated. This is simpler to build and is what the SDK in `frontend-sdk/` does by
   default (`TokenStore` is a small pluggable interface — swap in cookies later
   without touching the rest of the client).

**Do not** store the access token in `localStorage` if you can avoid it — it is
readable by any injected script (XSS) for its whole 15-minute life. If you do (many
real projects do, pragmatically), keep your CSP tight and be aware of the tradeoff.

### Refresh

`POST /api/v1/auth/token/refresh/` (`AllowAny`) — body: `{ "refresh": "<token>" }`.
Returns `{ access, refresh }` — **store both**, the old refresh token is now
blacklisted. On `AUTH_TOKEN_EXPIRED` / `AUTH_TOKEN_INVALID` from this endpoint, the
refresh token is dead — force a full logout/re-login.

### Automatic 401 handling

Attach `Authorization: Bearer <access>` to every authenticated request. When a request
comes back **`401`** — branch on the HTTP status, not on `errors.code`:
1. Call `/auth/token/refresh/` once (queue/dedupe concurrent 401s so you don't fire N
   parallel refresh calls).
2. Retry the original request with the new access token.
3. If refresh itself fails, clear stored tokens and redirect to login.

> **Don't gate this on `AUTH_TOKEN_EXPIRED` / `AUTH_TOKEN_INVALID`.** Those two codes
> are only raised by `/auth/token/refresh/` and `/auth/logout/`, which validate a
> *refresh* token explicitly. An expired **access** token is rejected by SimpleJWT's
> `InvalidToken`, which isn't in `_DRF_CODE_MAP` (`apps/core/exceptions.py`) and so
> comes back as `401` with `errors.code: "VALIDATION_ERROR"`. A code-gated interceptor
> never fires and logs the user out every 15 minutes. Full breakdown in
> [`ERROR-HANDLING.md`](./ERROR-HANDLING.md#the-401-rule-read-this-before-writing-the-auth-interceptor).

`frontend-sdk/api-client.ts` implements this flow via an axios response interceptor, but
still uses the narrower code-based condition (line 174) — widen it to a plain status
check.

### Logout

`POST /api/v1/auth/logout/` (`IsAuthenticated`) — body: `{ "refresh": "<token>" }`.
Blacklists the refresh token server-side. Always call this (not just clearing local
storage) so a stolen refresh token can't be replayed. Then clear local
access/refresh state and redirect to login.

### Password reset / email & phone verification

- `POST /api/v1/auth/password-reset/` — `{ "email": "..." }`. Always returns success
  (never leaks whether the email exists).
- `POST /api/v1/auth/password-reset-confirm/` — `{ "token": "...", "new_password": "..." }`.
- `POST /api/v1/auth/verify-email/` — `IsAuthenticated`, no body, resends to
  `request.user.email`.
- `POST /api/v1/auth/verify-email/confirm/` — `{ "token": "..." }` (token comes from
  the emailed link's query string).
- `POST /api/v1/auth/verify-phone/` — `IsAuthenticated`, optional `{ "phone": "..." }`
  body (defaults to the user's phone on file), sends an SMS OTP.
- `POST /api/v1/auth/verify-phone/confirm/` — `{ "code": "1234" }` (4-6 digits).
- `POST /api/v1/auth/change-password/` — `IsAuthenticated`,
  `{ "old_password": "...", "new_password": "..." }`.

### Profile

- `GET /api/v1/auth/me/` — returns `Me` (User + nested `profile`).
- `PATCH /api/v1/auth/me/` — partial update. To update profile fields
  (`date_of_birth`, `gender`, `marketing_consent`, `language`, `currency`), nest them
  under `profile: {...}` in the PATCH body; top-level `User` fields (`first_name`,
  `last_name`, `phone`) go at the top level.

## Request headers

| Header | When |
|---|---|
| `Content-Type: application/json` | All JSON bodies. |
| `Authorization: Bearer <access>` | Any authenticated endpoint. |
| `Idempotency-Key: <uuid>` | Required on `POST /checkout/` and admin `POST /payments/<id>/refund/`. Generate client-side with `crypto.randomUUID()`. |

### Idempotency-Key contract (important for checkout)

`apps/core/idempotency.py`: the key + a hash of the request body are cached
server-side. Reusing the same key with the **same** body replays the original stored
response (safe to retry on network failure). Reusing the same key with a **different**
body returns `409 IDEMPOTENCY_KEY_CONFLICT`. A concurrent duplicate mid-flight also
returns `409 IDEMPOTENCY_KEY_CONFLICT` — treat that as "retry shortly", not a hard
failure. **Generate one key per logical checkout attempt** (e.g. when the user lands
on the review-and-pay step) and reuse it across retries of that same attempt; generate
a new key if the user actually changes the cart/address/etc. and checks out again.

## Guest sessions & cookies

Cart, recently-viewed, and general browsing work for anonymous users via Django's
session cookie (not a JWT) — `apps/carts/services.py` uses `request.session.session_key`
when there's no authenticated user. This means:
- Your HTTP client **must send cookies** (`credentials: 'include'` for `fetch`,
  `withCredentials: true` for axios) even for logged-out users, or the guest cart will
  be a new empty cart on every request.
- `CORS_ALLOW_CREDENTIALS=True` server-side, and your frontend origin must be in
  `CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS` for this to work cross-origin.

## Products

`GET /api/v1/products/` (`AllowAny`, paginated, `search` throttle scope) — list of
`ProductListItem`. Query params (django-filter `ProductFilter` + DRF search/ordering):

| Param | Notes |
|---|---|
| `category` | category **slug** |
| `brand` | brand **slug** |
| `min_price` / `max_price` | on `price` |
| `product_type` | `simple`\|`variable`\|`bundle`\|`mix_and_match`\|`subscription` |
| `organic` | `true`/`false` — checks `honey_metadata.organic` or `food_metadata.organic` |
| `origin` | matches `honey_metadata.origin` or `food_metadata.country_of_origin` |
| `attribute` | `"<attribute_code>:<value_slug>"`, e.g. `weight:500g` |
| `on_sale` | `true` filters `compare_at_price > price` |
| `featured` | `true`/`false` |
| `search` | free text over name/sku/barcode/description (DRF `SearchFilter`) |
| `ordering` | `price`, `-price`, `created_at`, `-created_at`, `name_en` |
| `page`, `page_size` | pagination |

`GET /api/v1/products/{slug}/` — `ProductDetail`. Also records a "recently viewed"
entry for the current user/session as a side effect.

`GET /api/v1/products/{slug}/related/` — plain array (not paginated) of up to 12
`ProductListItem`s, same category, featured-first.

`GET /api/v1/products/recently-viewed/` — plain array, most-recent-first, tied to the
user (if logged in) or session.

**Gotchas:**
- `price`/`compare_at_price` are decimal **strings**.
- `computed_badges` includes stored badges (`NEW`, `BEST_SELLER`, etc.) plus
  dynamically computed `OUT_OF_STOCK`, `LOW_STOCK`, `SALE` — use this for badge UI, not
  raw `badges`.
- `in_stock` is `true` for products with no variants at all (edge case in the
  serializer) — don't assume a product without variants is always purchasable; check
  `product_type` and variant list length too.
- Images are absolute URLs (serializer is built with request context).

## Categories / Brands / Attributes

- `GET /api/v1/categories/` — flat list (not paginated), filterable by `?parent=<id>`,
  searchable by `?search=`.
- `GET /api/v1/categories/tree/` — full nested tree from roots (`children` recursive).
  Use this for a mega-menu/sidebar.
- `GET /api/v1/categories/{slug}/` — `CategoryDetail` incl. `breadcrumbs` (ancestor
  chain, self included, root first).
- `GET /api/v1/brands/` — paginated, searchable by `?search=`.
- `GET /api/v1/brands/{slug}/` — single brand.
- `GET /api/v1/attributes/` — plain array of filterable attributes with nested
  `values`; use this to build product filter UI (pair with the `attribute` product
  filter param above).

## Search

`GET /api/v1/search/?q=honey` (`AllowAny`, paginated, `search` throttle scope).
Postgres full-text search across name (en/ar), SKU, barcode, brand name, category
name, description (weighted). `q` is required — omitting it returns
`400 VALIDATION_ERROR`. Supports the same `ProductFilter` params as `/products/` (so
you can combine `q` with `category`, `min_price`, etc.) plus `ordering=rank` for
relevance sort.

## Cart

Works for both guest (session cookie) and authenticated (JWT) users — same endpoints,
the backend resolves the right cart. **Must send credentials** (see Guest sessions
above).

- `GET /api/v1/cart/` — current `Cart`.
- `DELETE /api/v1/cart/` — clear cart (also clears `coupon_code`).
- `POST /api/v1/cart/items/` — `{ "variant_id": 123, "quantity": 1 }`. Adding an
  already-present variant **increments** its quantity rather than erroring.
- `PATCH /api/v1/cart/items/{item_id}/` — `{ "quantity": 2 }`. **`quantity: 0` deletes
  the item** (not an error) — this is how you remove via the update endpoint.
- `DELETE /api/v1/cart/items/{item_id}/` — remove item outright.
- `POST /api/v1/cart/coupon/` — `{ "code": "SAVE10" }`. Validates against the current
  cart and stores `coupon_code` on the cart; validation errors are `INVALID_COUPON` /
  `COUPON_EXPIRED` / `COUPON_MINIMUM_NOT_MET` / etc.
- `DELETE /api/v1/cart/coupon/` — remove the applied coupon.
- `POST /api/v1/cart/merge/` — `IsAuthenticated`. Body: `{ "guest_cart_id": "<uuid>" }`.

### Guest cart → user cart merge flow

1. While the user is anonymous, `GET /api/v1/cart/` and keep the returned `id` (a
   UUID) around (e.g. in memory / sessionStorage) as `guestCartId`.
2. After a successful login (or register+login), call
   `POST /api/v1/cart/merge/` with `{ "guest_cart_id": guestCartId }`.
3. The backend sums quantities for variants present in both carts, copies over the
   guest cart's coupon code if the user cart has none, marks the guest cart `MERGED`,
   and returns the resulting user `Cart`. Refresh your cart state from this response.
4. If you don't have a `guest_cart_id` (e.g. fresh login, no prior guest browsing),
   calling merge with no body just returns the user's current cart — safe to call
   unconditionally after every login as a "sync" step.

**Prices are snapshotted** (`unit_price_snapshot`) at add-to-cart time and refreshed on
every quantity update — the cart does not silently reprice itself; call the cart
endpoints again if you need the latest price reflected.

## Wishlist

All `IsAuthenticated`.

- `GET /api/v1/wishlist/` — plain array of `WishlistItem` (product nested).
- `POST /api/v1/wishlist/add/` — `{ "product_id": 123 }` (idempotent — `get_or_create`).
- `DELETE /api/v1/wishlist/{product_id}/` — remove; 404 `NOT_FOUND` if absent.
- `GET /api/v1/wishlist/{product_id}/check/` — `{ "in_wishlist": boolean }`, for a
  product-page heart icon without loading the whole list.

## Addresses

All `IsAuthenticated`, standard `ModelViewSet` under `/api/v1/addresses/`
(**not** nested under `/auth/`):

- `GET /api/v1/addresses/` — plain array.
- `POST /api/v1/addresses/` — create (see `AddressInput` in `types.ts`; `governorate`
  is a Governorate **id**, not a code — fetch ids from
  `GET /api/v1/shipping/governorates/` first).
- `GET/PUT/PATCH/DELETE /api/v1/addresses/{id}/`
- `POST /api/v1/addresses/{id}/set-default/` — flips `is_default`.

`city` is optional (`null` allowed) — some governorates may not have a full city list
seeded.

## Shipping

- `GET /api/v1/shipping/governorates/` — plain array, active only.
- `GET /api/v1/shipping/cities/?governorate=<code>` — plain array; `governorate` query
  param is a Governorate **code** (e.g. `cairo`), not id.
- `GET /api/v1/shipping/quotes/?governorate=<code>&subtotal=<amount>&currency=EGP&weight_kg=<n>` —
  plain array of `ShippingQuote`, one per applicable `ShippingMethod`. `governorate`
  and `subtotal` are required. Use this to render shipping method choices with live
  prices before checkout (independent of the authoritative totals computed by
  `/checkout/summary/`).
- `GET /api/v1/shipping/track/{tracking_number}/` — `IsAuthenticated`, scoped to the
  requesting user's own orders.

## Checkout

Both endpoints take the same body (`CheckoutInput`):

```json
{
  "address_id": 12,
  "shipping_method_id": 3,
  "payment_method": "cod",
  "coupon_code": "SAVE10",
  "notes": "Leave at the door"
}
```

`payment_method` is one of `card | mobile_wallet | instapay | cod` — but do not
hard-code that list: ask `GET /payments/methods/`, which returns only what this
installation can complete
(`coupon_code` and `notes` are optional, default `""`).

1. `POST /api/v1/checkout/summary/` (`IsAuthenticated`) — **preview only**, does not
   touch the cart or create anything. Returns `CheckoutTotals`. Call this whenever the
   user changes address/shipping method/coupon on the checkout page so the displayed
   total is always server-computed (the frontend must never compute totals itself —
   tax, promotions, and coupon logic all live server-side).
2. `POST /api/v1/checkout/` (`IsAuthenticated`, **requires `Idempotency-Key` header**)
   — actually places the order: converts the cart to an `Order`, reserves stock,
   creates a `Payment` record, and — for `payment_method: "cod"` only — immediately
   marks the payment `captured` (COD has no gateway round-trip). Returns
   `CheckoutResult` = `{ order, payment }` with `status: 201`.

For non-COD methods, `payment.redirect_url` / `payment.iframe_url` (from
`payment.metadata`) is where you send the user to complete payment with the gateway;
after they return, poll `GET /api/v1/payments/{id}/status/` (see Payments below) —
**never** mark an order/payment as paid based on client-side redirect params alone.

**Known gap:** `CheckoutInput` has no fields for applying loyalty points, gift cards,
or store credit at checkout time, even though `Order` has
`loyalty_discount`/`gift_card_amount_used`/`store_credit_amount_used` columns (they
will always come back as `"0.00"` today). If your product requires "pay partially with
points/gift card" at checkout, that needs a backend change — don't build frontend UI
for it against the current API without confirming with backend first.

## Orders

All `IsAuthenticated`, scoped to `request.user`.

- `GET /api/v1/orders/` — paginated `OrderListItem[]`.
- `GET /api/v1/orders/{order_number}/` — `OrderDetail` (note: lookup is by
  `order_number`, e.g. `ORD-20260814-0001`, not the numeric `id`).
- `POST /api/v1/orders/{order_number}/cancel/` — `{ "reason": "Changed my mind" }`.
  Fails with `409 ORDER_CANNOT_CANCEL` if the order's current status doesn't allow it
  — cancellable from `pending`, `confirmed`, `processing`, `packed` only (see the
  state machine in `apps/orders/models.py::ORDER_STATUS_TRANSITIONS`); not cancellable
  once `shipped`+.
- `GET /api/v1/orders/returns/` — plain array of the user's `ReturnRequest`s across all
  orders.
- `POST /api/v1/orders/{order_number}/returns/` —
  ```json
  { "reason": "damaged", "description": "box was crushed", "items": [{ "order_item_id": 55, "quantity": 1 }] }
  ```
  `reason` is one of `damaged | wrong_product | missing_product | expired_product |
  quality_issue | customer_change_of_mind`.

Order status values and the exact allowed transitions are in
`apps/orders/models.py::ORDER_STATUS_TRANSITIONS` — use this to decide which actions
(cancel / track / return) to show for a given order.

## Payments

- `GET /api/v1/payments/{id}/` — `Payment` for one of the user's own orders.
- `GET /api/v1/payments/{id}/status/` — polls the provider for the latest status (if
  the provider supports live polling; otherwise relies on webhooks having already
  updated it) and returns the current `Payment`. **This is the only source of truth
  for "did the payment succeed"** — the backend explicitly documents that the frontend
  must never mark a payment successful itself based on redirect query params.
- `POST /api/v1/payments/{id}/refund/` — **admin-only** (`IsAdminUser`), not usable
  from a customer-facing storefront. Requires `Idempotency-Key`.
- `POST /api/v1/payments/webhooks/{provider}/` — inbound only, gateways call this, not
  the frontend.

### Recommended polling pattern after redirect-based payment

After the user returns from a card or wallet gateway redirect, poll
`GET /api/v1/payments/{id}/status/` every ~2s until `status` is `captured`, `failed`,
`cancelled`, or `expired`, with a reasonable timeout (~2 minutes) after which you tell
the user to check their orders page later (the webhook may still be in flight). See
`pollPaymentStatus()` in `frontend-sdk/payments.ts`.

## Reviews

- `GET /api/v1/products/{product_id}/reviews/` — `AllowAny`, paginated, only
  `approved=true` reviews (reviews are moderated before appearing).
- `POST /api/v1/reviews/` — `IsAuthenticated`:
  `{ "product_id": 1, "rating": 5, "title": "Great!", "comment": "..." }`. Returns
  `201` with the created review, but it won't be publicly visible until an admin
  approves it — tell the user "submitted for moderation", don't show it as live
  immediately.
- `POST /api/v1/reviews/{review_id}/helpful/` — `IsAuthenticated`, no body, increments
  `helpful_votes` (one vote per user, enforced by a unique constraint server-side).

## Coupons & Promotions

- `GET /api/v1/coupons/{code}/` — `AllowAny`. Preview a coupon's terms (discount type,
  minimum order, expiry) **before** applying it — use this for a "coupon details"
  tooltip. Applying it to the cart is a separate call: `POST /api/v1/cart/coupon/`.
- `GET /api/v1/promotions/` — `AllowAny`, plain array of currently-active automatic
  promotions (no code needed — the backend applies the best one at checkout time).
  Useful for a "current deals" banner, not for user interaction.

## Loyalty

All `IsAuthenticated`.

- `GET /api/v1/loyalty/` — `LoyaltyAccount` (`points_balance`, `lifetime_points_earned`,
  `tier_name`). Account/tier is auto-created on first access.
- `GET /api/v1/loyalty/transactions/` — paginated `LoyaltyTransaction[]`.
- `POST /api/v1/loyalty/redeem/` — `{ "points": 500 }` → `{ discount_value, currency }`.
  Note this only computes/reserves a discount value — see the checkout "known gap"
  above; there's currently no wiring to actually apply it to an order total.

## Gift cards & store credit

All `IsAuthenticated`.

- `POST /api/v1/gift-cards/check/` — `{ "code": "ABCD-1234-EFGH" }` → `GiftCard` with
  `masked_code`/`balance`/`status` (code is always masked in responses, even the one
  you just checked).
- `GET /api/v1/gift-cards/store-credit/` — `StoreCreditAccount` (`balance`).
- `GET /api/v1/gift-cards/store-credit/transactions/` — paginated
  `StoreCreditTransaction[]`.

(Same "not wired into checkout yet" caveat as loyalty — see Checkout section.)

## Subscriptions

All `IsAuthenticated`, "recurring box" style purchases.

- `GET /api/v1/subscriptions/` — plain array.
- `POST /api/v1/subscriptions/create/`:
  ```json
  {
    "name": "Monthly Honey Box", "address_id": 12, "shipping_method_id": 3,
    "payment_method": "cod", "frequency": "monthly", "start_date": "2026-09-01",
    "items": [{ "variant_id": 45, "quantity": 2 }]
  }
  ```
  `frequency: "custom"` requires `custom_interval_days`.
- `GET /api/v1/subscriptions/{id}/`
- `POST /api/v1/subscriptions/{id}/pause/`, `/resume/`, `/cancel/` — no body.

## Notifications

All `IsAuthenticated`.

- `GET /api/v1/notifications/` — paginated in-app notification feed (bell icon).
- `POST /api/v1/notifications/{id}/read/` — no body, marks read (idempotent).
- `GET /api/v1/notifications/preferences/` / `PATCH /api/v1/notifications/preferences/`
  — marketing opt-in flags (`marketing_email`, `marketing_sms`, `marketing_whatsapp`,
  `marketing_push`). Transactional notifications (order confirmations etc.) are not
  gated by these.

## Profile

Covered under Authentication above — `GET`/`PATCH /api/v1/auth/me/`.

## Media / image URLs

`ImageField`s (`product.images[].image`, `category.image`, `brand.logo`,
`user.profile_image`, review images) are serialized as **absolute URLs** (the views
pass request context into the serializer), so you can use them directly as `<img src>`
without prefixing a media host — just be aware they'll be `http://` in local dev if
you're not running behind TLS, which can trigger mixed-content warnings if your
frontend is on `https://`.
