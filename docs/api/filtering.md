# Filtering

All list endpoints use `django-filter` + DRF's `SearchFilter`/`OrderingFilter`.
Filters are combined with AND logic; multiple values for the same key are not
supported (send one value per filter key).

## Product filters — `GET /api/v1/products/`

| Param | Type | Example | Notes |
|---|---|---|---|
| `category` | string | `?category=honey` | Category slug |
| `brand` | string | `?brand=alreef-farms` | Brand slug |
| `min_price` / `max_price` | decimal | `?min_price=100&max_price=1000` | Applies to `Product.price` |
| `product_type` | string | `?product_type=variable` | `simple\|variable\|bundle\|mix_and_match\|subscription` |
| `organic` | boolean | `?organic=true` | Matches `honey_metadata.organic` or `food_metadata.organic` |
| `origin` | string | `?origin=Sinai` | Matches `honey_metadata.origin` or `food_metadata.country_of_origin` |
| `attribute` | string | `?attribute=weight:500g` | Format `attribute_code:value_slug`, matches product- or variant-level attribute values |
| `on_sale` | boolean | `?on_sale=true` | `price < compare_at_price` |
| `featured` | boolean | `?featured=true` | |

Example:

```
GET /api/v1/products/?category=honey&min_price=100&max_price=1000&ordering=-price
```

## Search — `GET /api/v1/search/?q=honey`

Accepts every product filter above **plus** the required `q` param. Ranked by
Postgres full-text search across name (EN/AR), SKU, barcode, brand name,
category name, and description (see `apps/search/views.py`).

## Ordering

Pass `?ordering=<field>` or `?ordering=-<field>` (descending). Supported
fields are declared per-view via `ordering_fields`; for products these are
`price`, `created_at`, `name_en` (and `rank` on the search endpoint).

## Category / Brand listing filters

- `GET /api/v1/categories/?parent=<id>` — filter by parent category id.
- `GET /api/v1/brands/` — supports `?search=<text>` (DRF `SearchFilter` over `name`).

## Combining with pagination

Filters compose with `?page=`/`?page_size=` — see `docs/api/pagination.md`.
