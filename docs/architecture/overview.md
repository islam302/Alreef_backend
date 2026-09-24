# Architecture Overview

Alreef is a Django REST Framework backend for an Egyptian e-commerce platform
specializing in honey, food, and grocery products. This document describes
the actual, implemented architecture — every component named here exists in
the codebase at the referenced path.

## Stack

- **Django 5.1** (`Django>=5.1,<5.2`) + **Django REST Framework 3.15**
- **PostgreSQL** (via `psycopg[binary]` 3.x) — the only supported database engine
  (`config/settings/base.py` hard-codes `django.db.backends.postgresql`)
- **Redis** — cache backend (`django-redis`) and Celery broker
- **Celery 5.4** + **django-celery-beat** (DB-backed periodic schedule) +
  **django-celery-results** (DB-backed result backend)
- **django-money 3.5** — every monetary field in the codebase is a `MoneyField`
  (amount + currency), never a bare `DecimalField`/`FloatField`. See "Money
  handling" below.
- **django-mptt** — the `Category` model is a materialized tree
  (`apps/categories/models.py`)
- **SimpleJWT 5.3** — stateless JWT auth with rotation + blacklist
- **drf-spectacular** — OpenAPI 3 schema generation, served with the sidecar
  Swagger/Redoc UI (no CDN dependency)
- **django-phonenumber-field** — validated Egyptian phone numbers
- **gunicorn** (WSGI) behind **nginx**; `whitenoise` serves static files with
  compressed manifest storage

## Django apps (`apps/`)

All 22 local apps are listed in `INSTALLED_APPS` in `config/settings/base.py`:

| App | Responsibility |
|---|---|
| `core` | Cross-cutting concerns: `BaseModel`/`TimeStampedModel`/`SoftDeleteModel` (`apps/core/models.py`), the response envelope (`apps/core/response.py`), error codes (`apps/core/error_codes.py`), the `Idempotency-Key` mixin (`apps/core/idempotency.py`), exception handler, pagination, logging filters |
| `users` | Custom `User` model (email login), `Address`, `CustomerProfile`, email/phone/password-reset token flows |
| `catalog` | `Product`, `ProductVariant`, `ProductImage`/`ProductVideo`, bundles, mix-and-match boxes, recently-viewed |
| `categories` | MPTT category tree |
| `brands` | `Brand` |
| `attributes` | `Attribute`/`AttributeValue` (variant-defining and descriptive) |
| `inventory` | `InventoryItem`, `Batch`, `StockReservation`, `InventoryMovement` — FEFO stock allocation |
| `carts` | `Cart`/`CartItem` (guest session or authenticated user) |
| `checkout` | Orchestration only — `calculate_totals`/`create_order_from_cart` (no models of its own) |
| `orders` | `Order`, `OrderItem`, `OrderStatusHistory`, `ReturnRequest`/`ReturnItem`, order status state machine |
| `payments` | `Payment`, `PaymentTransaction`, `PaymentWebhookEvent`, `Refund`, and the `PaymentProvider` adapter abstraction |
| `shipping` | `Governorate`/`City`/`ShippingZone`/`ShippingMethod`/`ShippingRate`, `Shipment`, the `ShippingProvider` adapter abstraction |
| `coupons` | `Coupon`, `CouponUsage` (code-based discounts) |
| `promotions` | `Promotion` (automatic, code-less discounts) |
| `reviews` | `Review`, `ReviewImage`, `ReviewHelpfulVote` |
| `wishlists` | `WishlistItem` |
| `subscriptions` | `Subscription`, `SubscriptionItem`, `SubscriptionSchedule` (recurring orders) |
| `loyalty` | `LoyaltyAccount`, `LoyaltyTransaction`, `LoyaltyTier`, configurable earn/redeem settings |
| `gift_cards` | `GiftCard`, `GiftCardTransaction`, `StoreCreditAccount`/`StoreCreditTransaction` |
| `notifications` | Email/SMS/WhatsApp provider abstractions, `Notification` (in-app), `NotificationLog` (delivery audit), Celery tasks |
| `search` | Postgres full-text search endpoint (`SearchVector`/`SearchQuery`/`SearchRank`) |
| `analytics` | Admin-facing reporting endpoints (`admin-api/`) |
| `audit` | `AuditLog` + `AuditLogMiddleware` — records API mutations and business-critical transitions |

## Request flow

```
Client
  │
  ▼
nginx (nginx/conf.d/default.conf)
  │  - serves /static/ and /media/ directly from shared volumes
  │  - proxies everything else to the "web" upstream
  ▼
gunicorn (config.wsgi:application, 4 workers)
  │
  ▼
Django middleware stack (config/settings/base.py MIDDLEWARE)
  SecurityMiddleware → WhiteNoise → CorsMiddleware → SessionMiddleware →
  LocaleMiddleware → CommonMiddleware → CsrfViewMiddleware →
  AuthenticationMiddleware → MessageMiddleware → XFrameOptionsMiddleware →
  RequestIDMiddleware (apps.core) → AuditLogMiddleware (apps.audit)
  │
  ▼
DRF view (JWTAuthentication, ScopedRateThrottle, DjangoFilterBackend)
  │
  ▼
apps/<app>/services.py  (business logic, @transaction.atomic where needed)
  │
  ▼
apps.core.response.success_response() / error_response()
```

All routes are namespaced under `/api/v1/` via `config/api_v1.py`, which
`include()`s each app's own `urls.py`.

### Standard API response envelope

Every endpoint returns the same JSON shape, produced by
`apps/core/response.py`:

```json
// success (apps.core.response.success_response)
{"success": true, "message": "Success", "data": { ... }, "errors": null}

// error (apps.core.response.error_response)
{"success": false, "message": "...", "data": null, "errors": {"code": "INSUFFICIENT_STOCK", "fields": {}}}
```

Business-rule failures are raised as `apps.core.error_codes.ApplicationError`
(a `code` + human message + optional `details`) from service-layer code and
converted to this envelope by the custom DRF exception handler
(`REST_FRAMEWORK["EXCEPTION_HANDLER"] = "apps.core.exceptions.custom_exception_handler"`).
The full set of machine-readable codes and their HTTP status mapping lives in
`apps/core/error_codes.py` (`ErrorCodes` / `ERROR_STATUS_MAP`).

Unsafe mutating endpoints that must not be double-executed on retry
(checkout, payment creation, refunds) require an `Idempotency-Key` header,
enforced by `apps.core.idempotency.IdempotentRequestMixin` — see
`docs/architecture/checkout.md` and `docs/architecture/payments.md`.

## Async flow (Celery)

Celery is configured in `config/settings/base.py`:

- Broker and result backend: Redis (`CELERY_BROKER_URL`) / Django DB
  (`CELERY_RESULT_BACKEND=django-db`)
- `CELERY_TASK_ACKS_LATE = True`, `CELERY_WORKER_PREFETCH_MULTIPLIER = 1` —
  tasks are only acknowledged after completion, one at a time per worker
  process, so a crashed worker does not silently drop in-flight work
- Queue routing (`CELERY_TASK_ROUTES`) separates traffic by domain so a burst
  of notification sends cannot starve inventory or payment reconciliation:

  | Queue | Tasks |
  |---|---|
  | `notifications` | `apps.notifications.tasks.*` |
  | `inventory` | `apps.inventory.tasks.*` |
  | `analytics` | `apps.analytics.tasks.*` |
  | `payments` | `apps.payments.tasks.*` |
  | `default` | everything else |

  The single worker container in `docker-compose.yml` consumes all five
  queues (`-Q default,notifications,inventory,analytics,payments`); queues
  can be split across dedicated worker containers later without any code
  change.

- `CELERY_BEAT_SCHEDULE` (also in `config/settings/base.py`), run by the
  `celery-beat` service:

  | Task | Interval | Purpose |
  |---|---|---|
  | `apps.inventory.tasks.release_expired_reservations_task` | 5 min | Frees stock held by abandoned checkouts |
  | `apps.inventory.tasks.deactivate_expired_batches_task` | hourly | Deactivates batches past `expiration_date` |
  | `apps.notifications.tasks.notify_expiration_alerts_task` | daily | Alerts on batches nearing expiry (7/14/30 days) |
  | `apps.notifications.tasks.process_abandoned_carts_task` | hourly | Emails carts inactive > `ABANDONED_CART_HOURS` |
  | `apps.payments.tasks.reconcile_pending_payments_task` | 15 min | Polls gateway status for stuck non-COD payments |
  | `apps.subscriptions.tasks.process_due_subscriptions_task` | daily | Places orders for due subscriptions |
  | `apps.loyalty.tasks.expire_stale_points_task` | daily | Expires loyalty points past their TTL |

## Money-handling policy

**Every monetary value in this codebase is a `djmoney.models.fields.MoneyField`**
— `Order.grand_total`, `OrderItem.unit_price`, `Payment.amount`,
`ShippingRate.base_price`, `Coupon.fixed_amount_value`, `GiftCard.balance`,
`LoyaltyProgramSettings.redemption_value_per_point`, etc. `MoneyField` stores
an amount plus an ISO currency code and returns a `djmoney.money.Money`
instance, so arithmetic (`item.line_total * rate`, `already_refunded + amount`)
carries currency along with it and cannot silently mix EGP with USD. Service
code (`apps/checkout/services.py`, `apps/payments/services.py`) constructs
`Money(0, currency)` as the zero/accumulator value rather than a bare `0`.
`DEFAULT_CURRENCY = "EGP"`, `CURRENCIES = ["EGP", "USD"]` in
`config/settings/base.py`; `EGYPT_VAT_RATE` (default `0.14`, i.e. 14%) is a
`Decimal` read from the environment, applied per line item based on
`Product.tax_category` (see `docs/architecture/checkout.md`).

There is no `FloatField` used for price/money anywhere in the models covered
by this documentation — using one for currency is the kind of rounding-error
bug `django-money` exists to prevent.

## Where to go next

- `docs/architecture/database.md` — full ERD and indexing rationale
- `docs/architecture/authentication.md` — JWT + verification/reset flows
- `docs/architecture/checkout.md` — end-to-end checkout sequence
- `docs/architecture/payments.md` — provider abstraction and honest status per gateway
- `docs/architecture/inventory.md` — FEFO reservation algorithm
- `docs/architecture/shipping.md` — zones/rates/provider abstraction
- `docs/architecture/notifications.md` — email/SMS/WhatsApp + Celery Beat
- `docs/architecture/scalability.md` — caching, N+1 prevention, known gaps
- `docs/payments/*.md` — one file per payment provider
- `docs/deployment/*.md` — production checklist and Docker Compose reference
