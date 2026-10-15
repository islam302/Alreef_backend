# Alreef — Egyptian E-Commerce Backend

A production-grade Django REST Framework backend for selling honey, dates,
nuts, oils, spices, grocery, and other natural/food products in Egypt, built
to extend internationally. Verified working end-to-end (auth, catalog, cart,
FEFO inventory, promotions/coupons, checkout, COD payment, order
confirmation) against real PostgreSQL and Redis.

## Contents

- [Architecture](#architecture)
- [Features](#features)
- [Requirements](#requirements)
- [Quick start (Docker)](#quick-start-docker)
- [Quick start (local, no Docker)](#quick-start-local-no-docker)
- [Environment variables](#environment-variables)
- [Database & migrations](#database--migrations)
- [Seed data](#seed-data)
- [Authentication](#authentication)
- [API documentation](#api-documentation)
- [Frontend integration](#frontend-integration)
- [Payments](#payments)
- [Shipping](#shipping)
- [Testing](#testing)
- [Security](#security)
- [Deployment](#deployment)
- [Production checklist](#production-checklist)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)

## Architecture

Modular Django apps under `apps/`, one bounded concern each:

`core` · `users` · `catalog` · `categories` · `brands` · `attributes` ·
`inventory` · `carts` · `checkout` · `orders` · `payments` · `shipping` ·
`coupons` · `promotions` · `reviews` · `wishlists` · `subscriptions` ·
`loyalty` · `gift_cards` · `notifications` · `search` · `analytics` · `audit`

Every endpoint lives under `/api/v1/` (see `config/api_v1.py`) and returns
the standard envelope:

```json
{ "success": true, "message": "Success", "data": { ... }, "errors": null }
```

See `docs/architecture/overview.md` for the full breakdown, `docs/architecture/database.md`
for an ERD, and `docs/architecture/checkout.md` / `payments.md` for sequence diagrams.

## Features

- Custom email/JWT auth (SimpleJWT, rotation + blacklist), email verification, phone OTP, password reset
- Egypt-focused addresses (27 database-driven governorates + cities)
- Flexible product catalog: variants, attributes (no-migration extensibility), bundles, mix-and-match boxes, food/honey-specific JSON metadata, badges
- FEFO (First-Expired-First-Out) batch inventory with concurrency-safe reservations (`select_for_update`)
- Cart (guest + authenticated, merge-on-login), wishlist, recently viewed, related products
- Coupons + automatic promotions (percentage, fixed, free shipping, buy-X-get-Y, category/brand scoped)
- Reviews gated to verified (delivered-order) purchases, with photo upload and moderation
- Checkout with backend-authoritative totals (tax, shipping, discounts — the frontend never computes money), idempotent order creation
- Order state machine with returns/refunds workflow
- Payment provider abstraction — **COD and InstaPay are live**; the Paymob adapter (card + mobile wallet) is complete against Paymob's official documentation but stays disabled until a merchant account and verified credentials exist (see [Payments](#payments))
- Shipping zones/methods/rates (flat/weight/price-based, free-shipping thresholds), provider abstraction (manual fulfillment live; Bosta/Aramex skeletons)
- Loyalty points, gift cards, store credit
- Subscriptions (recurring orders) with Celery-driven billing cycles
- Notifications: email (Django templates), SMS + WhatsApp provider abstraction (console backend by default), in-app notifications, delivery audit log
- Admin dashboard metrics, analytics event stream, audit log (API mutations + Django admin's built-in LogEntry)
- OpenAPI 3 schema + Swagger UI + ReDoc, Postman collection, TypeScript types/SDK

## Requirements

- Python 3.13+
- PostgreSQL 16 (uses `django.contrib.postgres` full-text search + `ArrayField` — Postgres-only)
- Redis 7
- Docker + Docker Compose (recommended path)

## Quick start (Docker)

```bash
cp .env.example .env          # edit SECRET_KEY etc. for anything beyond local dev
docker compose up --build
```

This brings up `postgres`, `redis`, `web` (runs migrate + collectstatic +
gunicorn automatically), `celery`, `celery-beat`, and `nginx` (port 80).
Then, in another terminal:

```bash
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py seed_data
```

API: `http://localhost/api/v1/` · Swagger: `http://localhost/api/docs/` ·
ReDoc: `http://localhost/api/redoc/` · Admin: `http://localhost/admin/`

> **Port note:** `docker-compose.yml` publishes Postgres on host port
> **5433**, not 5432 — this avoids clashing with a native PostgreSQL install
> that may already be running on your machine. Internally (container-to-
> container) it's still `postgres:5432`; only host-side tools (e.g. a local
> `psql` or a non-Dockerized Django) need `5433`.

## Quick start (local, no Docker)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements/development.txt
cp .env.example .env            # set DB_HOST=localhost, DB_PORT matching your Postgres

# Postgres + Redis via Docker even if the app runs locally:
docker compose up -d postgres redis

python manage.py migrate
python manage.py seed_data
python manage.py runserver
```

## Environment variables

See `.env.example` — every setting is documented there with its default.
Highlights: `SECRET_KEY`, `DEBUG`, `DB_*`, `REDIS_URL`, `JWT_ACCESS_MINUTES`/
`JWT_REFRESH_DAYS`, `EMAIL_*`, `SMS_PROVIDER`/`WHATSAPP_PROVIDER`, the
`PAYMOB_*`/`FAWRY_*`/`VALU_*`/`COD_*` blocks, `BOSTA_*`/`ARAMEX_*`,
`SENTRY_DSN`, `EGYPT_VAT_RATE`.

## Database & migrations

```bash
python manage.py makemigrations --check   # part of the acceptance criteria — should report no changes
python manage.py migrate
```

Money fields use `django-money` (`MoneyField`), never `FloatField` — see
`docs/architecture/database.md`.

## Seed data

```bash
python manage.py seed_data
```

Creates: 27 Egyptian governorates, 2 shipping zones (Cairo & Giza / Rest of
Egypt) with standard + express rates, 9 categories, 4 brands, weight/origin
attributes, 6 real food/honey products with variants + inventory batches, a
`WELCOME10` coupon, a "Honey Week" promotion, an admin user, and a test
customer with a saved address. **Credentials are printed to the console**
(also below) — this is local/demo data only, never real customer data.

| Account | Email | Password |
|---|---|---|
| Admin | `admin@alreef.example` | `ChangeMe123!` |
| Test customer | `customer@alreef.example` | `ChangeMe123!` |

**Change or remove these before any shared/staging deployment.**

## Authentication

JWT via SimpleJWT: 15-minute access tokens, 14-day refresh tokens, rotation
+ blacklist enabled. Full flow (register → verify email → login → refresh →
logout) documented in `docs/api/authentication.md` and
`docs/architecture/authentication.md`.

## API documentation

- **OpenAPI 3 schema:** `GET /api/schema/`
- **Swagger UI:** `GET /api/docs/`
- **ReDoc:** `GET /api/redoc/`
- **Narrative docs:** `docs/api/*.md` (one file per domain: authentication,
  products, categories, cart, checkout, payments, orders, shipping,
  coupons, reviews, returns, content, errors, pagination, filtering,
  image-upload, webhooks)
- **Architecture docs:** `docs/architecture/*.md`
- **Payment provider notes:** `docs/payments/*.md`
- **Deployment docs:** `docs/deployment/*.md`

## Frontend integration

`docs/frontend/` is a self-contained guide for whoever builds the storefront:

| File | Covers |
|---|---|
| `SETUP.md` | Getting the API running locally, seed data, test accounts, common failures |
| `QUICKSTART.md` | login→browse→cart→checkout→pay→view-order walkthrough |
| `README.md` | Full integration guide, per domain |
| `ENDPOINTS.md` | Every route: auth, pagination shape, throttle scope |
| `ERROR-HANDLING.md` | Error codes → UI, the 401/refresh rule, idempotent checkout retries |
| `STATES.md` | Every enum, the order state machine, which actions to show when |
| `PAYMENTS.md` | Checkout and payment flows, and which providers actually work today |
| `DATA-FETCHING.md` | React Query / SSR patterns, cache keys, invalidation |
| `I18N.md` | Arabic/RTL, bilingual field coverage, money and date formatting |
| `types.ts` | TypeScript interfaces for every payload |

`frontend-sdk/` is a ready-to-use axios client (auth, products, cart, checkout,
orders, payments modules) implementing the same flows.

## Payments

Provider abstraction at `apps/payments/providers/` (`PaymentProvider` ABC:
`create_payment` / `authorize` / `capture` / `cancel` / `refund` /
`get_status` / `verify_webhook` / `parse_webhook_event`).

Four methods, and no others. Ask `GET /payments/methods/` rather than hard-coding
this table: it returns only what this installation can actually complete.

| Method | Route | Status |
|---|---|---|
| **Cash on delivery** | none | ✅ Live. Captured when the courier marks the order delivered — not at checkout. |
| **InstaPay** | `instapay` | ✅ Live once `INSTAPAY_ADDRESS` is set. InstaPay publishes no merchant API, so the customer transfers from their banking app and a staff member confirms receipt. |
| **Visa / Mastercard / Meeza** | `paymob` | ⚙️ Complete against Paymob's official docs, disabled until a merchant account exists. |
| **Vodafone Cash** and other wallets | `paymob` | ⚙️ Same Paymob adapter, same requirement. |

Fawry, valU, Sympl and Aman were implemented and then **removed** at the business's
request. They are gone from the settings, the adapters, the model choices and both
frontends; tests assert that none of those codes resolves to an adapter.

Run `python manage.py check_payments` to see, per method, whether it is offerable and
exactly which setting is missing if it is not. This project deliberately does not
fabricate undocumented gateway request/response formats — see `docs/payments/*.md`.

Idempotency: `POST /checkout/` and `POST /payments/{id}/refund/` require an
`Idempotency-Key` header (`apps/core/idempotency.py`). Webhooks are
deduplicated by `PaymentWebhookEvent.provider_event_id` uniqueness.

## Shipping

Database-driven Egyptian governorates/cities, zone → method → rate pricing
(flat / weight-based / free-shipping-threshold), provider abstraction at
`apps/shipping/providers/` (`manual` fully live; `bosta`/`aramex` skeletons
gated the same way as the payment gateways above).

## Testing

```bash
pytest                    # or: .venv\Scripts\python.exe -m pytest
pytest --cov=apps         # with coverage (config in .coveragerc)
```

**151 tests, all passing**, across every domain app:

| File | Covers |
|---|---|
| `test_auth.py` | register/login/refresh/logout/blacklist, me, change password, account lockout |
| `test_security.py` | upload-validator wiring, fail-closed webhook defaults, throttle-scope coverage |
| `test_catalog.py` | product visibility, filters, category tree, search |
| `test_cart.py` | guest/auth cart, merge-on-login, coupon apply |
| `test_coupons.py` | validation, scoping (category/brand), minimum order, per-user limits |
| `test_promotions.py` | active-window filtering, percentage + buy-X-get-Y math |
| `test_inventory_fefo.py` | FEFO batch allocation, spill-over, consume/release |
| `test_inventory_concurrency.py` | last-unit race (sequential `select_for_update` proof) |
| `test_checkout_and_orders.py` | full COD checkout, idempotency, cancel, state machine |
| `test_returns.py` | return window policy, quantity limits, refund completion |
| `test_reviews.py` | delivered-purchase eligibility, duplicate prevention, moderation |
| `test_wishlists.py` | add/remove/check, idempotent duplicate add |
| `test_loyalty.py` | points earning rate, redemption limits, balance math |
| `test_gift_cards.py` | issue/redeem, balance caps, expiry, store credit |
| `test_subscriptions.py` | create/pause/resume/cancel, recurring order generation |
| `test_shipping.py` | governorates/cities, rate calculation, tracking ownership |
| `test_payments.py` | COD provider, refund capping, idempotent refunds, webhook amount-mismatch rejection |
| `test_notifications.py` | in-app list/read scoping, preferences, delivery logging |
| `test_api_response_envelope.py` | the `{success, message, data, errors}` contract itself |

Test settings (`config/settings.testing`) use a separate `test_alreef`
database, eager (synchronous, propagating) Celery, locmem cache/email, and
the MD5 password hasher for speed.

### Continuous Integration

`.github/workflows/tests.yml` runs on every push/PR to `main`: spins up real
Postgres + Redis service containers, runs `manage.py check`,
`makemigrations --check --dry-run`, the full `pytest` suite with coverage
(uploaded as a build artifact), and validates the OpenAPI schema generates
cleanly (`manage.py spectacular --validate`).

## Security

The backend went through a full security pass (auth, authorization/IDOR,
throttling, webhooks, headers, uploads, injection, secrets, admin, data
exposure, audit logging, dependencies). Summary of what's in place:

- **Auth**: JWT (15min access / 14day refresh, rotation + blacklist),
  PBKDF2 password hashing, Django's standard password validators, single-use
  cryptographically random tokens for email verification / password reset
  (1hr TTL) and hashed OTPs for phone verification (10min TTL, 5-attempt
  cap). Password-reset requests never leak whether an email is registered.
- **Brute-force protection**: two independent layers. `ScopedRateThrottle`
  scopes (`auth`: 20/min, `auth_confirm`: 10/min for token/OTP-guessing
  endpoints) plus a blanket `AnonRateThrottle`/`UserRateThrottle` baseline so
  *no* endpoint is ever fully unthrottled — and a per-account cache-backed
  lockout (`apps/users/services.py`) that locks login for 15 minutes after
  10 failed attempts on the same email, independent of the caller's IP.
- **Authorization**: every user-owned resource (addresses, orders, payments,
  carts, wishlists, subscriptions, loyalty, gift cards, reviews,
  notifications) is scoped to `request.user` in its queryset/service layer;
  admin-only mutations (refunds, analytics dashboard) require
  `IsAdminUser`. `AddressViewSet` additionally uses the `IsOwner` object
  permission as defense in depth.
- **Webhooks**: signatures are verified *before* any DB write or trust
  decision (`apps/payments/services.py::process_webhook`), events are
  idempotent via a unique `provider_event_id`, and a webhook-reported amount
  is rejected if it disagrees with the server-computed `payment.amount`.
  Both payment and shipping provider base classes fail **closed**
  (`verify_webhook` defaults to reject) so a future adapter that forgets to
  override signature verification doesn't silently accept forged callbacks.
- **File uploads**: every `ImageField` that accepts user/staff content
  (products, categories, brands, reviews, video thumbnails, user avatars)
  runs through `validate_image_file` — extension allowlist, 5MB size cap,
  declared-MIME check, and a real Pillow `image.verify()` + dimension check
  (guards against decompression bombs and spoofed extensions), not just an
  extension check.
- **Transport & headers**: `SECURE_SSL_REDIRECT`, HSTS (1yr,
  includeSubDomains, preload), secure+httponly+SameSite cookies in
  `config/settings/production.py`; `nginx/conf.d/default.conf` sets
  `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy` explicitly on
  `/static/`/`/media/`, since those bypass Django's `SecurityMiddleware`
  entirely. CORS is an explicit origin allowlist (never wildcard).
  Production refuses to boot on the insecure default `SECRET_KEY`
  (`config/settings/production.py` raises `ImproperlyConfigured`).
- **Injection**: no raw SQL, `eval`/`exec`, unsafe deserialization, or
  `shell=True` anywhere in `apps/` — verified by full-codebase grep. All
  querying goes through the Django ORM.
- **Audit trail**: `AuditLog` rows are immutable in Django admin (no add,
  change, or delete) and record order status transitions and refunds with
  full detail; a middleware safety net logs every authenticated API
  mutation. Admin-panel-only mutations are covered by Django's built-in
  `LogEntry`.
- **Logging**: `SensitiveDataFilter` redacts passwords/tokens/secrets/card
  data from every log line; provider webhook payloads are redacted before
  being persisted to `PaymentTransaction.raw_response`.

Regression tests for this pass live in `tests/test_security.py` plus
additions to `tests/test_auth.py` and `tests/test_payments.py` (account
lockout, upload-validator wiring, fail-closed webhook defaults, webhook
amount-mismatch rejection).

## Deployment

See `docs/deployment/docker.md` and `docs/deployment/production-checklist.md`.
Summary: build the production image (`Dockerfile`, defaults to
`requirements/production.txt`), run behind `nginx` (`nginx/conf.d/default.conf`)
with TLS terminated at your load balancer or nginx itself, set
`DJANGO_SETTINGS_MODULE=config.settings.production`, real `SECRET_KEY`/
`ALLOWED_HOSTS`/`SECURE_SSL_REDIRECT`, and run `migrate` + `collectstatic`
on release (the `web` service's Docker Compose command already does this).

## Production checklist

See `docs/deployment/production-checklist.md` for the full list. Headline
items: rotate the seeded admin/customer passwords (or don't seed them at
all in production), set real payment/shipping credentials only once
verified against each provider's current official docs, configure
`SENTRY_DSN`, set up Postgres backups, and confirm `python manage.py check
--deploy` is clean.

## Troubleshooting

- **`password authentication failed` connecting to Postgres on port 5432** —
  you likely have a native PostgreSQL service already bound to 5432; this
  repo's Docker Compose publishes Postgres on host port **5433** instead
  (see Quick start above).
- **`makemigrations` prints a `RuntimeWarning` about migration history** —
  harmless if Postgres isn't reachable yet from the *host* (e.g. you only
  started `docker compose up -d postgres` and haven't exposed 5433 to your
  shell's env vars) — set `DB_HOST=localhost DB_PORT=5433` for host-side
  manage.py commands, or run them via `docker compose exec web ...` instead.
- **Money fields serialize oddly** — `apps/core/apps.py` registers
  django-money's DRF integration (`register_money_field()`) so
  `MoneyField` model fields render as plain decimal strings (e.g.
  `"150.00"`) in every `ModelSerializer` automatically; don't remove that.

## Known limitations

- The Paymob integration (card, mobile wallet) and the Bosta/Aramex shipping
  adapters require real merchant credentials, which are not available in this
  environment. The Paymob adapter is written against Paymob's current official
  documentation and tested against fixtures of those documented shapes, but it
  has never been run against a Paymob environment — even their sandbox needs
  merchant onboarding. It therefore ships **disabled** and must not be described
  as live until its sandbox flow, webhook verification, failure path, refund path
  and idempotency checks have all passed against the real thing (see
  [Payments](#payments) and `docs/payments/paymob.md`).
- Product full-text search computes a `SearchVector` per query rather than
  using a stored, GIN-indexed `SearchVectorField` — fine at seed-data scale,
  worth optimizing before a large catalog (see `docs/architecture/scalability.md`).
- Loyalty-points redemption and gift-card/store-credit balances are exposed
  via API but not yet wired as a deductible line item inside
  `apps/checkout/services.calculate_totals` — currently applied ad hoc by
  the frontend calling `/loyalty/redeem/`, `/gift-cards/check/` separately;
  integrating them directly into the checkout totals response is a
  reasonable next iteration.
- There's no dedicated public "approve/reject return" staff API — that
  workflow is currently Django-admin-only (bulk actions on `ReturnRequest`).
- WhatsApp/SMS ship with a `console` (log-only) provider by default; wiring
  a real vendor means implementing `SMSProvider`/`WhatsAppProvider` against
  that vendor's current docs (see `apps/notifications/providers/`).

---

Built for the Egyptian market (EGP currency, `Africa/Cairo` timezone,
Arabic-first `LANGUAGE_CODE`) with English + multi-currency/country support
designed in from the start (`apps/shipping` and `apps/catalog` don't assume
Egypt anywhere in the schema — only the seed data and defaults do).
