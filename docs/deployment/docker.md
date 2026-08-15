# Docker

Source: `docker-compose.yml`, `Dockerfile`, `nginx/conf.d/default.conf`.

## Services

`docker-compose.yml` defines six services:

### `postgres`

```yaml
image: postgres:16-alpine
environment:
  POSTGRES_DB: ${DB_NAME:-alreef}
  POSTGRES_USER: ${DB_USER:-alreef}
  POSTGRES_PASSWORD: ${DB_PASSWORD:-alreef}
volumes:
  - postgres_data:/var/lib/postgresql/data
ports:
  - "5433:5432"
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U ${DB_USER:-alreef} -d ${DB_NAME:-alreef}"]
  interval: 10s
  timeout: 5s
  retries: 5
```

Note the **host** port mapping is `5433:5432` — deliberately offset from
Postgres's default 5432 "to avoid clashing with a native PostgreSQL install
that may already occupy 5432 on the host machine" (comment in
`docker-compose.yml`). Other containers on the Docker network still reach it
at `postgres:5432` (the internal port is unchanged) — only `DB_HOST=postgres`,
`DB_PORT=5432` is correct *inside* the Compose network (set explicitly for
the `web`/`celery`/`celery-beat` services' `environment:` block). If you run
Django locally outside Docker against this container, use `DB_HOST=localhost`,
`DB_PORT=5433`.

Data persists in the named volume `postgres_data`.

### `redis`

```yaml
image: redis:7-alpine
volumes:
  - redis_data:/data
healthcheck:
  test: ["CMD", "redis-cli", "ping"]
```

Backs the cache, sessions, idempotency locks, and the Celery broker (see
`docs/architecture/scalability.md`). Data persists in `redis_data`.

### `web`

```yaml
build:
  context: .
  args:
    REQUIREMENTS_FILE: requirements/production.txt
env_file: .env
environment:
  DJANGO_SETTINGS_MODULE: config.settings.production
  DB_HOST: postgres
  REDIS_URL: redis://redis:6379/0
  CELERY_BROKER_URL: redis://redis:6379/0
volumes:
  - static_volume:/app/staticfiles
  - media_volume:/app/media
depends_on:
  postgres: {condition: service_healthy}
  redis: {condition: service_healthy}
command: >
  sh -c "python manage.py migrate --noinput &&
         python manage.py collectstatic --noinput &&
         gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4 --timeout 60"
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/api/health/"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 20s
```

The Django app server. It only starts after `postgres` and `redis` report
healthy (`depends_on: condition: service_healthy`, not just "started"). Its
startup command runs migrations and `collectstatic` **every time the
container starts** before launching gunicorn — this makes deploys
self-migrating (no separate migration step needed) at the cost of every
`web` replica racing to run the same migration on scale-out; for a
multi-replica production deployment you would typically split "run
migrate/collectstatic once" from "start gunicorn" into a separate init step,
but as shipped it's a single combined command suitable for a single-replica
or sequential-rollout deployment. `static_volume` and `media_volume` are
shared with `nginx` (read-only there) so nginx can serve them directly
without proxying through Django.

### `celery`

```yaml
build: {context: ., args: {REQUIREMENTS_FILE: requirements/production.txt}}
env_file: .env
environment: {DJANGO_SETTINGS_MODULE: config.settings.production, DB_HOST: postgres, REDIS_URL: redis://redis:6379/0, CELERY_BROKER_URL: redis://redis:6379/0}
volumes:
  - media_volume:/app/media
depends_on:
  postgres: {condition: service_healthy}
  redis: {condition: service_healthy}
command: celery -A config worker --loglevel=info --concurrency=4 -Q default,notifications,inventory,analytics,payments
healthcheck:
  test: ["CMD-SHELL", "celery -A config inspect ping -d celery@$$HOSTNAME || exit 1"]
```

A single worker container, concurrency 4, consuming **all five** queues
(`default,notifications,inventory,analytics,payments` — see
`docs/architecture/overview.md` for what routes to each). It mounts
`media_volume` (not `static_volume`) because some tasks may need to read/
write uploaded media (e.g. processing an uploaded review image) but never
serve static assets. The healthcheck uses Celery's own `inspect ping`
against the specific worker hostname (`celery@$HOSTNAME`, escaped as `$$HOSTNAME`
for Compose's variable interpolation).

For higher throughput or stricter isolation between queues (so a
notification backlog can never delay payment reconciliation), this single
service can be split into multiple `celery` service definitions each with a
different `-Q` argument (e.g. a dedicated `celery-payments` worker with
`-Q payments`) — the queue routing config already supports this; only the
Compose file would need to change.

### `celery-beat`

```yaml
build: {context: ., args: {REQUIREMENTS_FILE: requirements/production.txt}}
env_file: .env
environment: {...same as celery...}
depends_on:
  postgres: {condition: service_healthy}
  redis: {condition: service_healthy}
command: celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

Runs the periodic task scheduler (`CELERY_BEAT_SCHEDULE` in
`config/settings/base.py`) using the database-backed scheduler
(`django_celery_beat`), so schedule entries can also be viewed/edited via
Django admin, not just the settings file. **Exactly one** `celery-beat`
instance should ever run at a time — running two would double-fire every
scheduled task (duplicate emails, duplicate reconciliation runs, etc.); this
is a general Celery Beat constraint, not something this Compose file
enforces for you.

### `nginx`

```yaml
image: nginx:1.27-alpine
ports:
  - "80:80"
volumes:
  - ./nginx/conf.d:/etc/nginx/conf.d:ro
  - static_volume:/app/staticfiles:ro
  - media_volume:/app/media:ro
depends_on:
  web: {condition: service_healthy}
```

Reverse proxy in front of gunicorn. From `nginx/conf.d/default.conf`:

- `/static/` → served directly from the `static_volume`, `expires 30d`,
  access logging disabled.
- `/media/` → served directly from the `media_volume`, `expires 7d`, access
  logging disabled.
- everything else → `proxy_pass http://django_backend` (upstream
  `web:8000`), forwarding `Host`, `X-Real-IP`, `X-Forwarded-For`,
  `X-Forwarded-Proto` (the last of which `config/settings/production.py`
  relies on via `SECURE_PROXY_SSL_HEADER` — see
  `docs/deployment/production-checklist.md`).
- `client_max_body_size 10M` — matches `FILE_UPLOAD_MAX_MEMORY_SIZE` /
  `DATA_UPLOAD_MAX_MEMORY_SIZE` (5 MB) headroom in Django settings for
  product image uploads etc.

Only listens on port 80 (plain HTTP) — this Compose file alone does not
terminate TLS. Put a TLS-terminating proxy/load balancer in front for any
real deployment (see the production checklist).

## Dockerfile

```dockerfile
FROM python:3.13-slim AS base
...
RUN apt-get install -y libpq-dev gcc curl
COPY requirements/ requirements/
ARG REQUIREMENTS_FILE=requirements/production.txt
RUN pip install --no-cache-dir -r ${REQUIREMENTS_FILE}
COPY . .
RUN useradd --create-home --uid 1000 appuser && mkdir -p /app/staticfiles /app/media && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health/ || exit 1
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60"]
```

- Single-stage build on `python:3.13-slim`.
- `libpq-dev`/`gcc` are build dependencies for `psycopg[binary]` and any
  C-extension packages; `curl` is installed specifically so the container's
  own `HEALTHCHECK` can call `/api/health/`.
- `REQUIREMENTS_FILE` is a build arg, defaulting to
  `requirements/production.txt` — `docker-compose.yml` passes this
  explicitly for `web`/`celery`/`celery-beat`; a development image could
  build with `requirements/development.txt` instead by overriding this arg.
- Runs as a non-root user (`appuser`, uid 1000) — the image never runs the
  app as root.
- The image-level `HEALTHCHECK` and the Compose-level `healthcheck:` for
  `web` both hit the same `/api/health/` endpoint; Compose's version takes
  precedence when running under Compose, but the image also works correctly
  standalone (e.g. run directly under plain `docker run` or another
  orchestrator that reads Docker's native `HEALTHCHECK`).

## Running it

```bash
# 1. Copy and fill in environment variables
cp .env.example .env
# edit .env: SECRET_KEY, ALLOWED_HOSTS, DB_*, payment/shipping creds, etc.

# 2. Build and start everything
docker compose up --build

# 3. (subsequent runs, no rebuild needed)
docker compose up -d

# 4. Check health
docker compose ps
curl http://localhost/api/health/
```

The `web` service's own startup command already runs `migrate` and
`collectstatic` on boot, so no separate manual step is required for a fresh
environment — though re-running them manually is safe (both are idempotent
Django management commands).

## Running management commands

Exec into the running `web` container:

```bash
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py shell
docker compose exec web python manage.py migrate          # re-run manually if needed
docker compose exec web python manage.py collectstatic --noinput
```

For one-off commands without a long-running container already up:

```bash
docker compose run --rm web python manage.py <command>
```

To inspect Celery worker/beat state:

```bash
docker compose logs -f celery
docker compose logs -f celery-beat
docker compose exec celery celery -A config inspect active
```

## Volumes

| Volume | Mounted by | Purpose |
|---|---|---|
| `postgres_data` | `postgres` | Database files |
| `redis_data` | `redis` | Redis persistence (RDB/AOF per Redis's own config) |
| `static_volume` | `web` (rw), `nginx` (ro) | `collectstatic` output, served by nginx |
| `media_volume` | `web` (rw), `celery` (rw), `nginx` (ro) | User-uploaded files (product/review/profile images) |

All four are named volumes managed by Compose (not bind mounts), declared at
the bottom of `docker-compose.yml`. For a horizontally-scaled deployment
across multiple hosts, `static_volume`/`media_volume` (and `postgres_data`/
`redis_data`, more importantly) would need to move to shared/managed storage
(S3 for media — see `USE_S3` in the production checklist; a managed
Postgres/Redis service for the data volumes) since local Docker volumes are
host-local.
