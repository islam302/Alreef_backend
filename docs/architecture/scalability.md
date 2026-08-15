# Scalability

This document describes what the codebase actually does for performance and
scale today, and is explicit about known gaps rather than implying more
optimization has happened than has.

## Database connection handling

`CONN_MAX_AGE` (persistent connections) is set in `config/settings/base.py`:
`60` seconds by default (`DB_CONN_MAX_AGE` env var), raised to `600` seconds
in `config/settings/production.py`. This avoids a fresh TCP + auth handshake
to Postgres on every request under gunicorn's multi-worker model, without
introducing an external pooler. There is no PgBouncer/pgpool configuration
in this repo — for very high connection-count deployments (many gunicorn
workers × many Celery workers, each holding a connection), adding a
connection pooler in front of Postgres would be the next step; it is not
present today.

## Caching (Redis / django-redis)

`CACHES["default"]` uses `django_redis.cache.RedisCache` against `REDIS_URL`
with a `KEY_PREFIX = "alreef"` (`config/settings/base.py`) — namespacing
prevents key collisions if the same Redis instance is ever shared with
another app. Redis currently backs three concrete, implemented uses in this
codebase:

1. **Sessions**: `SESSION_ENGINE = "django.contrib.sessions.backends.cache"`,
   `SESSION_CACHE_ALIAS = "default"` — session reads/writes never hit
   Postgres.
2. **Idempotency locks**: `apps.core.idempotency.IdempotentRequestMixin`
   stores the in-flight/completed request state in `cache` (see
   `docs/architecture/checkout.md`).
3. **Celery broker**: `CELERY_BROKER_URL` defaults to the same `REDIS_URL`.

**Not yet done**: there is no `@cache_page` / low-level `cache.get_or_set()`
wrapping around the read-heavy product listing/detail/search endpoints
(`apps/catalog/views.py`, `apps/search/views.py`) in the code reviewed. Given
these are `AllowAny`, high-read, low-write endpoints, they are strong
candidates for response caching (keyed by query params + a cache-busting
version tied to product/category updates) as a future optimization — the
Redis infrastructure to do so is already in place, it's just unused for this
specific purpose today.

## Celery queue routing

Four dedicated queues plus `default`, configured in
`config/settings/base.py` `CELERY_TASK_ROUTES` (detail in
`docs/architecture/overview.md`): `notifications`, `inventory`, `analytics`,
`payments`. This means a burst of notification-sending cannot starve the
5-minute stock-reservation-release sweep or the 15-minute payment
reconciliation sweep, even though today's `docker-compose.yml` runs a single
`celery` container consuming all queues (`-Q default,notifications,inventory,analytics,payments`)
— splitting them across dedicated worker containers/replica counts under
load is a deployment-time change, not a code change, because the routing is
already in place. `CELERY_TASK_ACKS_LATE = True` +
`CELERY_WORKER_PREFETCH_MULTIPLIER = 1` trade a small amount of throughput
for correctness: a task is only considered done after it actually completes,
and a worker only prefetches one task at a time, so a crashed worker process
doesn't lose or unfairly hoard queued work.

## N+1 prevention

`select_related`/`prefetch_related` are used deliberately at the query
construction sites that serialize nested relations, rather than left to
per-serializer-field lazy loading. The clearest example is
`apps/catalog/views.py`:

```python
PRODUCT_LIST_PREFETCH = ["images", "variants__attribute_values__attribute", "variants__images", "attribute_values__attribute"]

def _published_products():
    return (
        Product.objects.filter(status=ProductStatus.ACTIVE, published=True)
        .select_related("brand", "category")
        .prefetch_related(*PRODUCT_LIST_PREFETCH)
    )
```

`_published_products()` is the single source queryset shared by
`ProductListView`, `ProductDetailView`, and `RelatedProductsView` — every
product-serving endpoint gets the same prefetch coverage rather than each
view redefining (and potentially forgetting) it. `select_related("brand",
"category")` avoids a query per product for its FK fields; the four
`prefetch_related` paths avoid a query per product for its images and its
variants' nested attribute values — without this, listing 20 products with
3 variants each could trigger on the order of 100+ queries instead of ~5.

The same pattern appears elsewhere: `apps/search/views.py` prefetches
`("images", "variants")` on its search queryset; `apps/checkout/services.py`
calls `cart.items.select_related("variant__product")` before iterating cart
items (avoiding a query per line item for its variant/product); `apps/users/views.py`
`AddressViewSet.get_queryset()` uses `select_related("governorate", "city")`.

## Pagination caps

`apps.core.pagination.StandardResultsPagination` (the sole
`DEFAULT_PAGINATION_CLASS`) sets `page_size = 20` and, critically,
`max_page_size = 100` — a client cannot request `?page_size=100000` and force
the database/serializer to materialize an unbounded result set. This bounds
both query cost and response payload size regardless of what a client asks
for.

## Read-heavy endpoint characteristics

Product listing (`ProductListView`), detail (`ProductDetailView`), and
search (`ProductSearchView`) are all `permissions.AllowAny` and read-only —
the natural candidates for edge/CDN or application-level caching layers. No
`ETag`/`Cache-Control` response headers or CDN configuration are set for
these routes in the code reviewed; `nginx/conf.d/default.conf` only sets
`expires`/cache headers for `/static/` and `/media/`, not for API responses.

## Known scaling gap: search relies on a computed `SearchVector`

`apps/search/views.py:ProductSearchView.get_queryset()` builds its
full-text-search vector **per query, at request time**:

```python
vector = SearchVector("name_en", weight="A") + SearchVector("name_ar", weight="A") + \
    SearchVector("sku", weight="B") + SearchVector("barcode", weight="B") + \
    SearchVector("brand__name", weight="C") + SearchVector("category__name_en", weight="C") + \
    SearchVector("description", weight="D")
search_query = SearchQuery(query)

return (
    Product.objects.filter(status=ProductStatus.ACTIVE, published=True)
    .annotate(rank=SearchRank(vector, search_query))
    .filter(rank__gt=0)
    ...
)
```

This is functionally correct (weighted multi-field search across name,
SKU/barcode, brand, category, description with proper A/B/C/D relevance
ranking) but Postgres must compute `to_tsvector(...)` for every candidate row
on every search request — there is no stored, GIN-indexed `SearchVectorField`
column being maintained (e.g. via a `django.contrib.postgres.indexes.GinIndex`
+ a trigger or a `SearchVectorField` updated on save/via a signal). This is
fine at the current catalog scale but is a genuine, honest scaling gap: as
the product catalog grows, search latency will grow with it because the
`SearchVector` computation is not pre-materialized or indexed. The
straightforward future optimization is adding a persisted `SearchVectorField`
on `Product`, populating/maintaining it via a signal or a periodic task, and
adding a `GinIndex` on it — turning the per-query vector computation into a
single indexed lookup. This is called out here deliberately rather than
glossed over.

## Summary of what's real vs. what's a stated gap

| Concern | Status |
|---|---|
| DB connection reuse (`CONN_MAX_AGE`) | Implemented |
| Redis-backed sessions | Implemented |
| Redis-backed idempotency locking | Implemented |
| Celery queue isolation by domain | Implemented (routing configured; single worker container consumes all queues by default in `docker-compose.yml`) |
| N+1 prevention on product listing/search/checkout | Implemented (`select_related`/`prefetch_related` at defined call sites) |
| Pagination cap (`max_page_size=100`) | Implemented |
| HTTP response caching on read-heavy product/search endpoints | **Not implemented** — Redis is available but unused for this |
| Persisted/indexed full-text search vector | **Not implemented** — computed per-query; documented as a future optimization |
| Connection pooler (PgBouncer) in front of Postgres | **Not implemented** — relies solely on `CONN_MAX_AGE` |
