# Production Checklist

This checklist is grounded in `config/settings/base.py`,
`config/settings/production.py`, `.env.example`, `docker-compose.yml`, and
`Dockerfile`. It does not invent settings that aren't in the codebase.

## 1. Core Django settings

- [ ] Set `DJANGO_SETTINGS_MODULE=config.settings.production` (the
  `web`/`celery`/`celery-beat` services in `docker-compose.yml` already set
  this; if deploying outside Docker, set it explicitly).
- [ ] Set `DEBUG=False`. `config/settings/production.py` hard-codes
  `DEBUG = False` regardless of the env var, so this is enforced by using
  the production settings module — but never rely on that alone; also set
  `DEBUG=False` in `.env` for clarity and for any tooling that reads it
  directly.
- [ ] Set a real, long, random `SECRET_KEY` — the default in
  `config/settings/base.py` (`"django-insecure-change-me-in-env"`) must
  never be used in production. This key also signs JWTs
  (`SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY`), so a leaked/default key
  compromises both CSRF protection and auth tokens.
- [ ] Set `ALLOWED_HOSTS` to your real domain(s) — `production.py` requires
  this explicitly (`config("ALLOWED_HOSTS", cast=Csv())`, no default), so
  the app will fail to start without it.
- [ ] Set `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` to your real
  frontend origin(s) — both default to `http://localhost:3000`, which must
  not remain in production.
- [ ] Set `FRONTEND_URL` — used to build links in outbound emails
  (verification, password reset). This is the *only* source of the host in
  those links: the endpoints deliberately ignore any URL in the request
  body, because honouring one would let anyone have us mail a victim a real
  reset token pointed at their own server. If this is wrong, reset links are
  broken; if it were caller-supplied, they would be an account-takeover
  primitive. See `apps/core/frontend_links.py`.
- [ ] Leave `API_DOCS_ENABLED` unset (it defaults to `DEBUG`, so off in
  production). `/api/schema/`, `/api/docs/` and `/api/redoc/` are not
  mounted at all when it is off — the schema documents every admin route
  and is a complete map of the back office. Set it to `True` on a staging
  box if the frontend team needs Swagger there.

## 2. Transport security (`config/settings/production.py`)

These are automatically enabled by using the production settings module —
verify them rather than assume:

- [ ] `SECURE_SSL_REDIRECT` (default `True` via `SECURE_SSL_REDIRECT` env)
- [ ] `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True`
- [ ] `SECURE_HSTS_SECONDS` (default 31536000 = 1 year),
  `SECURE_HSTS_INCLUDE_SUBDOMAINS = True`, `SECURE_HSTS_PRELOAD = True`
- [ ] `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` +
  `USE_X_FORWARDED_HOST = True` — **only correct if the reverse proxy in
  front of Django actually sets `X-Forwarded-Proto`**; the bundled
  `nginx/conf.d/default.conf` does set `X-Forwarded-Proto $scheme` on
  proxied requests, but if you put another load balancer/proxy in front of
  that nginx, confirm it forwards this header too, or Django will think
  every request is insecure and redirect-loop.
- [ ] Terminate TLS somewhere in the chain — neither the bundled nginx
  config nor gunicorn terminate HTTPS themselves (nginx here only listens on
  port 80). Put a TLS-terminating reverse proxy or load balancer in front
  (e.g. a managed load balancer, or add a `443 ssl` server block with real
  certificates to `nginx/conf.d/default.conf`) before exposing this to the
  internet.

## 3. Database

- [ ] Point `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_PORT` at a real,
  managed or self-hosted PostgreSQL instance — not the `postgres:16-alpine`
  container's default `alreef`/`alreef`/`alreef` credentials from
  `docker-compose.yml`, which are fine for local dev only.
- [ ] Run `python manage.py migrate` (the `web` service's startup command in
  `docker-compose.yml` already does this on every boot —
  `sh -c "python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn ..."` —
  but confirm it succeeds and don't skip it if deploying manually).
- [ ] Run `python manage.py collectstatic --noinput` (also already in the
  `web` service startup command) — required for `whitenoise` to serve
  compressed, hashed static files and for nginx's `/static/` location to
  have content.
- [ ] Create a superuser: `python manage.py createsuperuser` — note
  `apps.users.managers.UserManager.create_superuser` forces
  `email_verified=True`, so this account is never blocked by the
  email-verification flow.
- [ ] Load governorate/city seed data — `apps/shipping/models.py` defines
  the canonical 27-governorate `EGYPT_GOVERNORATES` list; ensure it (and at
  least one `ShippingZone`/`ShippingMethod`/`ShippingRate`) is loaded before
  going live, since `Address` creation and checkout both require a valid
  `Governorate`.
- [ ] Set `DB_CONN_MAX_AGE` appropriately — `production.py` already raises
  it to 600s.

## 4. Redis / Celery

- [ ] Point `REDIS_URL` and `CELERY_BROKER_URL` at a real Redis instance
  (not the bundled `redis:7-alpine` container for anything beyond
  local/staging).
- [ ] Ensure the `celery` worker service is running and consuming all
  required queues — `docker-compose.yml`'s command is
  `celery -A config worker --loglevel=info --concurrency=4 -Q default,notifications,inventory,analytics,payments`.
  Without this running, no order confirmation emails, stock-reservation
  expiry, payment reconciliation, or abandoned-cart notifications will fire.
- [ ] Ensure `celery-beat` is running (`django_celery_beat.schedulers:DatabaseScheduler`)
  — this is what actually triggers the scheduled tasks in
  `CELERY_BEAT_SCHEDULE` (`config/settings/base.py`). A missing beat process
  means expired stock reservations are never released and expired batches
  are never deactivated.
- [ ] `CELERY_RESULT_BACKEND=django-db` — confirm `django_celery_results`
  migrations have run (covered by step 3's `migrate`).

## 5. Payment providers

Run this first. It answers "which methods will customers see" and, for each one
they will not, exactly which setting is empty:

```bash
python manage.py check_payments          # exits 1 if NO method is available
python manage.py check_payments --strict # exits 1 if ANY method is unavailable
```

Full setup detail per provider, including the merchant-side steps, is in
[`../payments/PROVIDERS.md`](../payments/PROVIDERS.md).

### Always

- [ ] Set **`BACKEND_PUBLIC_URL`** to this API's real public origin. Providers call
  our webhooks out of band, so this cannot be derived from a request — and
  `check_payments` prints the webhook URLs from it. If it says `localhost`, the URLs
  you register with Paymob will too.
- [ ] Leave **`PAYMENT_ALLOWED_CURRENCIES=EGP`** unless you genuinely sell in
  another currency. Gateway integration IDs are currency-specific: a mismatch
  charges the wrong amount *successfully*, and nothing notices until a
  reconciliation weeks later.
- [ ] Confirm **`PAYMENT_WEBHOOK_MAX_AGE_HOURS`** (default 72). Providers retry for
  hours; anything older than this is recorded but not applied.
- [ ] Verify Celery Beat is running (step 4). Without it
  `reconcile_pending_payments_task` never fires, and a customer whose webhook was
  dropped stays unconfirmed until somebody notices by hand.

### Cash on delivery

- [ ] **Live as shipped** (`COD_ENABLED=True`). Confirm `COD_FEE_AMOUNT`,
  `COD_MIN_ORDER_AMOUNT` and `COD_MAX_ORDER_AMOUNT` match your real rules — all
  three are now enforced at checkout, before an order exists. `COD_MAX_ORDER_AMOUNT=0`
  means no ceiling.
- [ ] Set `COD_ALLOWED_SHIPPING_METHOD_IDS` if any carrier you use will not handle
  cash. Empty means any.
- [ ] Know that a COD payment is **`pending` until collection**, and that moving an
  order to `DELIVERED` is what captures it. Staff can also record it, or a refusal
  at the door, from the dashboard. If your operations team was trained on the old
  behaviour — where a COD payment looked paid the moment the order was placed —
  tell them.

### InstaPay

InstaPay is live as a **bank transfer the customer makes and a staff member
confirms**. There is no API and no webhook: InstaPay publishes no merchant
acceptance API, and Paymob lists their InstaPay integration as coming soon. See
[`../payments/instapay.md`](../payments/instapay.md).

- [ ] Set `INSTAPAY_ENABLED=True` and `INSTAPAY_ADDRESS` to the shop's InstaPay
  address (e.g. `alreef@instapay`). The address is **mandatory**: without it the
  method is hidden rather than offered with nowhere to send the money.
- [ ] Set `INSTAPAY_ACCOUNT_NAME` so a cautious customer can check the name in
  their banking app before transferring.
- [ ] **Train the team on the one rule that matters: do not ship an InstaPay order
  until the transfer is confirmed in the dashboard.** The order stays `pending` and
  its stock stays reserved until somebody posts the confirmation, and nothing in
  the system can verify the transfer for them.
- [ ] Tell them to refuse a short transfer rather than confirm it. The server
  checks nothing here — it cannot — so confirming a short payment records money the
  shop does not have.
- [ ] Agree who checks the InstaPay app and how often. `INSTAPAY_CONFIRM_WINDOW_HOURS`
  is only a note to staff; nothing enforces it.

### Paymob

Card and mobile wallet. The adapter is implemented against Paymob's current
official documentation, and **has never been run against a real Paymob
environment**, because that requires merchant onboarding. Do not enable it on
production until you have completed the checklist in
[`../payments/PROVIDERS.md`](../payments/PROVIDERS.md), which ends with a sandbox
transaction, a verified callback, a refund and a failure path.

- [ ] Setting `PAYMOB_ENABLED=True` is **not enough**. The methods stay hidden
  until `PAYMOB_SECRET_KEY`, `PAYMOB_PUBLIC_KEY`, `PAYMOB_HMAC_SECRET` and at least
  one `PAYMOB_INTEGRATION_ID_*` are all present. That is deliberate: a gateway that
  can take money it cannot verify is worse than one switched off.
- [ ] `PAYMOB_SECRET_KEY` and `PAYMOB_API_KEY` are **different credentials**. The
  second is only used for refunds, voids and status inquiries — without it the shop
  can take card payments and not refund them, which the dashboard shows per
  payment.
- [ ] Register the Paymob **Transaction Processed Callback** (server-to-server), not
  the Transaction Response Callback:
  `https://<your-domain>/api/v1/payments/webhooks/paymob/`. Point the *response*
  callback at the storefront's `/order/payment-return` instead.
- [ ] Set `PAYMOB_LIVE=True` when the keys are production keys. It changes no URL,
  but it is recorded on every payment, so a live transaction created with a test key
  is visible rather than merely wrong.
- [ ] Leave `PAYMOB_INTEGRATION_ID_INSTAPAY` empty until InstaPay is live on the
  account. Setting it moves InstaPay from the manual route to Paymob automatically.

### Before you announce a payment method is live

- [ ] `python manage.py check_payments` lists it as available.
- [ ] `GET /payments/methods/` on the public API shows it, and the storefront's
  checkout offers it.
- [ ] You placed a real order with it and the money actually arrived where you
  expected.

For a gateway method (card, wallet) additionally:

- [ ] A sandbox transaction completed and its callback arrived and verified.
- [ ] A deliberately failed transaction left the order unconfirmed.
- [ ] A refund executed, and an over-refund was refused.
- [ ] The same webhook delivered twice changed nothing the second time.

For a manually-settled method (COD, InstaPay) additionally:

- [ ] Staff can find the order in the dashboard's `محتاجة إجراء` filter and confirm
  it, and the order moved to confirmed as a result.
- [ ] Somebody other than you has done that once, unaided.

## 6. Shipping providers

- [ ] Manual (self-fulfilled) shipping works out of the box — no
  configuration needed.
- [ ] Leave `BOSTA_ENABLED=False` / `ARAMEX_ENABLED=False` (or don't set
  them) until a real adapter is implemented (`apps/shipping/providers/bosta.py`
  is currently a skeleton; there is no `aramex.py` adapter at all yet). See
  `docs/architecture/shipping.md`.

## 7. Monitoring — Sentry

- [ ] Set `SENTRY_DSN` — `config/settings/production.py` initializes
  `sentry_sdk` automatically if this is non-empty, with `DjangoIntegration`,
  `CeleryIntegration`, and `RedisIntegration`, and
  `send_default_pii=False` (does not send personally identifiable request
  data to Sentry by default).
- [ ] Set `SENTRY_TRACES_SAMPLE_RATE` (default `0.1` = 10% of transactions)
  and `ENVIRONMENT` (used as Sentry's `environment` tag) to appropriate
  values for your deployment.

## 8. Email / SMS / WhatsApp

- [ ] Set `EMAIL_BACKEND` to a real SMTP backend (not the default
  `django.core.mail.backends.console.EmailBackend`, which only logs) plus
  `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`,
  `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`. Without this, registration,
  password reset, order confirmation, and every other transactional email
  are silently swallowed (console backend just prints/logs them).
- [ ] Set `SMS_PROVIDER` to a real vendor and implement a corresponding
  `SMSProvider` subclass (`apps/notifications/providers/sms_base.py`) — only
  `console` (log-only) is implemented today. Required for phone OTP
  verification to actually reach customers.
- [ ] Set `WHATSAPP_PROVIDER=meta_cloud_api` plus
  `WHATSAPP_PHONE_NUMBER_ID`/`WHATSAPP_ACCESS_TOKEN` and implement a real
  `WhatsAppProvider` subclass (`apps/notifications/providers/whatsapp_base.py`)
  — only `console` is implemented today.

## 9. File storage

- [ ] Decide on `USE_S3` — `False` (default) uses local disk
  (`FileSystemStorage`, served by the `media_volume` Docker volume + nginx's
  `/media/` location); `True` switches to
  `storages.backends.s3.S3Storage` and requires
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_STORAGE_BUCKET_NAME`/
  `AWS_S3_REGION_NAME`/`AWS_S3_CUSTOM_DOMAIN`. For a multi-instance/scaled
  deployment, local disk storage does not survive across containers/hosts —
  use S3 (or an equivalent shared object store) rather than the local-disk
  default.

## 10. Backups

The scripts live in the umbrella repository, `deploy/server/` (see its README).

- [ ] Schedule `deploy/server/backup.sh` on the host (cron, 03:00). It dumps the
  database, tars the media, refuses a dump with no data in it, keeps 14 days,
  and emails `OPS_ALERT_EMAIL` when anything fails.
- [ ] **Send the backups off this host.** A backup on the same disk as the
  database protects you from a bad migration, not from losing the machine. Set
  `OFFSITE_REMOTE` and the `RCLONE_CONFIG_*` lines in `.env` (Cloudflare R2
  steps in `deploy/server/README.md`); the copies are encrypted with
  `BACKUP_PASSPHRASE`, which must also be kept somewhere else.
- [ ] **Rehearse the restore, and write down how long it took.**

      deploy/server/restore.sh --check /srv/backups/alreef/db-<stamp>.sql.gz

  This restores into a throwaway database beside the live one, counts orders,
  payments, customers and products, and drops it again. It touches nothing
  live, so it can run monthly.
- [ ] Know the real-emergency path before you need it:
  `deploy/server/restore.sh --overwrite-production <db file> [<media file>]`,
  which stops the application, replaces the live database (and media), and
  demands the word RESTORE typed in full.
- [ ] The `postgres_data` Docker volume is not a backup strategy.
- [ ] Back up the `media_volume` (user-uploaded product images, review
  images, profile images) if using local disk storage rather than S3.

## 11. Final pre-launch checks

- [ ] Confirm `docker compose --profile production up -d` (or your actual
  deployment mechanism) brings up `postgres`, `redis`, `web`, `celery`,
  `celery-beat`, and `nginx` all healthy (`docker compose ps` — each service
  has a healthcheck defined in `docker-compose.yml`).
- [ ] Hit `GET /api/health/` — backed by `django-health-check` (checks DB,
  cache, storage; `health_check`, `health_check.db`, `health_check.cache`,
  `health_check.storage` are installed apps) — both directly against `web`
  and through `nginx`, since both `Dockerfile` and `docker-compose.yml`
  healthchecks poll this endpoint.
- [ ] Confirm the OpenAPI schema serves correctly
  (`SPECTACULAR_SETTINGS["SERVE_INCLUDE_SCHEMA"] = True`) if you intend to
  expose API docs, and decide whether Swagger/Redoc UI should be
  publicly reachable in production.
- [ ] Walk through the real, verified-working flow once against production
  config: register → verify email → browse → add to cart → checkout with
  COD → confirm the order and stock deduction both happened correctly.
