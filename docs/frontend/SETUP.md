# Running the backend locally

Written for a frontend developer who needs a working API on localhost and does not
intend to write Python. Everything here is one of two commands plus the seed data.

## What you need

- Docker + Docker Compose. That's the whole list if you take the Docker path.
- Without Docker you'd also need Python 3.13+, PostgreSQL 16 and Redis 7 — the app uses
  Postgres-only features (full-text search, `ArrayField`), so SQLite is not an option.

## The fast path

```bash
git clone <repo> && cd Alreef_back
cp .env.example .env
docker compose up --build
```

First build takes a few minutes. The `web` service runs migrations and
`collectstatic` on its own, so when it settles the API is up.

In a second terminal, load the demo catalogue:

```bash
docker compose exec web python manage.py seed_data
```

That gives you Egyptian governorates, shipping zones/methods/rates, categories, brands,
attributes, honey and food products with variants and inventory batches, one coupon, one
promotion, and two user accounts.

### Where things are

| | URL |
|---|---|
| API root | `http://localhost/api/v1/` |
| Swagger UI | `http://localhost/api/docs/` |
| ReDoc | `http://localhost/api/redoc/` |
| OpenAPI schema | `http://localhost/api/schema/` |
| Django admin | `http://localhost/admin/` |
| Health check | `http://localhost/api/health/` |

Docker publishes through nginx on **port 80**, so there's no `:8000`. If you run the
dev server directly instead (below), everything moves to `http://localhost:8000`.

Postgres is published on host port **5433**, not 5432, to avoid clashing with a native
install. Only matters if you're pointing a DB GUI at it.

### Seeded accounts

| Email | Password | Role |
|---|---|---|
| `admin@alreef.example` | `ChangeMe123!` | superuser — Django admin, admin-only endpoints |
| `customer@alreef.example` | `ChangeMe123!` | ordinary customer |

Use the customer account for storefront work. Local dev only, obviously.

The customer is seeded with `email_verified=True` and one default Cairo address, so you
can go straight from login to checkout without setting anything up.

## Without Docker

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows; source .venv/bin/activate elsewhere
pip install -r requirements/development.txt
cp .env.example .env             # set DB_HOST=localhost, DB_PORT to your Postgres port

docker compose up -d postgres redis    # still the easiest way to get the services

python manage.py migrate
python manage.py seed_data
python manage.py runserver       # http://localhost:8000
```

With `DEBUG=True` you also get DRF's Browsable API — hitting `http://localhost:8000/api/v1/products/`
in a browser gives a clickable HTML interface, handy for poking at query params without
a client.

## Settings your frontend cares about

All in `.env`, all already correct for a Next.js app on port 3000:

```ini
CORS_ALLOWED_ORIGINS=http://localhost:3000
CSRF_TRUSTED_ORIGINS=http://localhost:3000
FRONTEND_URL=http://localhost:3000
```

Change the port your app runs on and you must change these too, or every request fails
at the CORS preflight — which shows up in the browser as a network error with no status
code, not as a 4xx you can read.

`CORS_ALLOW_CREDENTIALS` is `True` in `config/settings/base.py`. That is what makes the
guest cart work: the anonymous cart is keyed to the Django **session cookie**, so your
HTTP client must send cookies on every request, logged in or not.

```ts
axios.create({ baseURL, withCredentials: true });   // axios
fetch(url, { credentials: "include" });             // fetch
```

Forget this and the guest cart silently resets on every call — you add an item, the next
`GET /cart/` returns an empty cart, and nothing errors.

Other knobs worth knowing:

| Variable | Default | Why you'd care |
|---|---|---|
| `JWT_ACCESS_MINUTES` | 15 | Raise it locally if you're tired of testing refresh. Don't ship it raised. |
| `JWT_REFRESH_DAYS` | 14 | |
| `COD_ENABLED` | `True` | Set `False` to test the `PAYMENT_PROVIDER_UNAVAILABLE` path. |
| `COD_FEE_AMOUNT` | `15.00` | Change it and confirm your UI reads `summary.cod_fee` rather than a hardcoded 15. |
| `EGYPT_VAT_RATE` | `0.14` | |
| `IDEMPOTENCY_KEY_TTL_SECONDS` | 86400 | How long a checkout key replays. |
| `DEBUG` | `True` | Off means no Browsable API and no media served by Django. |

## Emails, SMS and OTPs in dev

`EMAIL_BACKEND` is the console backend and `SMS_PROVIDER`/`WHATSAPP_PROVIDER` are
`console`. Nothing is actually sent — everything prints to the server log. So to verify
an email or complete a phone OTP:

```bash
docker compose logs -f web        # Docker
# or just read the runserver terminal
```

Then copy the token out of the printed message:

- **Email verification** — the log contains a link ending
  `/api/v1/auth/verify-email/confirm/?token=<token>`. Take the token, `POST` it to that
  endpoint (or just open the link).
- **Password reset** — same idea, token goes to `/auth/password-reset-confirm/`.
- **Phone OTP** — the 4-6 digit code is printed; `POST` it to
  `/auth/verify-phone/confirm/`.

There's no "get the last OTP" API endpoint. The log is the only place.

## Background jobs

`docker compose up` also starts `celery` and `celery-beat`. Most of what they do is
invisible to the frontend (emails, stock reservation cleanup, subscription billing), but
two things are worth knowing:

- Expired stock reservations are released every 5 minutes. If you leave a checkout
  half-finished, stock frees up on that cadence.
- Abandoned-cart processing runs hourly against carts idle for `ABANDONED_CART_HOURS`
  (6 by default).

Running without Docker and without a Celery worker, `.delay()` calls queue into Redis
and nobody consumes them. Nothing breaks — API responses don't wait on them — you just
never see the console emails. Start a worker if you need them:

```bash
celery -A config worker -l info
```

## Poking at the API

Three options, in increasing order of effort:

**Swagger** (`/api/docs/`) — generated from the live code, so it can't be out of date.
Click *Authorize* and paste a `Bearer <access>` token to call authenticated endpoints.

To try requests by hand, Swagger above is the source of truth; any client that can import OpenAPI (Postman, Insomnia, Bruno) can load `/api/schema/` directly, so a hand-kept collection never drifts from the code.

**curl** — for a quick sanity check that the stack is alive:

```bash
curl http://localhost/api/v1/products/ | head -c 400

curl -X POST http://localhost/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email":"customer@alreef.example","password":"ChangeMe123!"}'
```

## Generating types instead of hand-writing them

[`types.ts`](./types.ts) is maintained by hand and covers everything the storefront
needs. If you'd rather generate:

```bash
npx openapi-typescript http://localhost/api/schema/ -o src/api/schema.d.ts
```

The generated types describe the raw envelope, so you'll still want the `ApiResponse<T>`
/ `PaginatedResponse<T>` wrappers from `types.ts` on top. Generated names are also long
and auto-derived, which is why the hand-written file exists.

## When something doesn't work

| Symptom | Cause |
|---|---|
| Browser network error, no status code, "CORS" in the console | Your origin isn't in `CORS_ALLOWED_ORIGINS`. Restart the backend after editing `.env`. |
| Guest cart empties between calls | Missing `withCredentials` / `credentials: "include"`. |
| `401` on every authenticated call | No `Authorization: Bearer <access>` header, or the access token is past 15 minutes — see the 401 rule in [`ERROR-HANDLING.md`](./ERROR-HANDLING.md#the-401-rule-read-this-before-writing-the-auth-interceptor). |
| `429 THROTTLED` while developing | `auth` is 20/min and `search` 60/min. Hot-reload loops hit these fast. Wait a minute. |
| Empty product list | You skipped `seed_data`, or products exist but aren't `published`. |
| `503 PAYMENT_PROVIDER_UNAVAILABLE` at checkout | You picked a method other than `cod`. Expected — see [`PAYMENTS.md`](./PAYMENTS.md#what-works-right-now). |
| Images 404 in dev | Media is only served by Django when `DEBUG=True`. |
| `docker compose up` fails on port 80 | Something else is on it. Change the nginx port mapping in `docker-compose.yml`. |

## Next

- [`QUICKSTART.md`](./QUICKSTART.md) — wire up a Next.js app and place a real COD order.
- [`README.md`](./README.md) — the full integration guide.
- [`ENDPOINTS.md`](./ENDPOINTS.md) — every route at a glance.
