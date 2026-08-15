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
  (verification, password reset).

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

- [ ] **COD is production-ready as-is** (`COD_ENABLED=True` by default) —
  confirm `COD_FEE_AMOUNT`, `COD_MIN_ORDER_AMOUNT`, `COD_MAX_ORDER_AMOUNT`
  reflect your real business rules.
- [ ] **Do not set `PAYMOB_ENABLED=True`, `FAWRY_ENABLED=True`, or
  `VALU_ENABLED=True`** until the corresponding adapter
  (`apps/payments/providers/{paymob,fawry,valu}.py`) has actually been
  implemented against that provider's verified, official API documentation
  with real sandbox-tested credentials — as shipped, these adapters raise
  `NotImplementedError` for every operation including webhook signature
  verification. Enabling the flag without implementing the adapter does not
  make the gateway work; it only changes which error message customers see.
  See `docs/architecture/payments.md` and `docs/payments/*.md`.
- [ ] Once a gateway adapter is genuinely implemented and tested: set its
  `_ENABLED=True` plus its credential env vars (`PAYMOB_API_KEY` /
  `PAYMOB_INTEGRATION_ID_CARD` / `PAYMOB_INTEGRATION_ID_WALLET` /
  `PAYMOB_IFRAME_ID` / `PAYMOB_HMAC_SECRET`; `FAWRY_MERCHANT_CODE` /
  `FAWRY_SECURITY_KEY`; `VALU_API_KEY`) and register its webhook URL
  (`https://<your-domain>/api/v1/payments/webhooks/<provider>/`) with the
  provider.

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

- [ ] Set up regular PostgreSQL backups (e.g. `pg_dump`/WAL archiving, or
  your managed database provider's automated backup feature) — nothing in
  this repository automates database backups; the `postgres_data` Docker
  volume alone is not a backup strategy.
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
