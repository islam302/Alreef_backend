# Products, Brands, Attributes, Search

Base paths: `/api/v1/` (products, search), `/api/v1/brands/`, `/api/v1/attributes/`.

Source: `apps/catalog/{urls,views,filters,serializers,models}.py`, `apps/brands/*`, `apps/attributes/*`, `apps/search/*`. See `docs/api/categories.md` for category endpoints.

All product/brand/attribute endpoints are `AllowAny` (public, read-only). Product listing and search share the same `ProductFilter` and are throttled at `search` scope (**60/min**).

## Product model shape

`Product` (`apps/catalog/models.py`) fields relevant to the API:

- `product_type`: `simple | variable | bundle | mix_and_match | subscription`
- `status`: `draft | active | archived` — only `status=active, published=True` products are ever returned publicly
- `price` / `compare_at_price` / `cost_price`: `djmoney` `MoneyField`, EGP by default (`cost_price` is never serialized to the API)
- `tax_category`: `standard | zero_rated | exempt` — drives VAT at checkout (`EGYPT_VAT_RATE`, default 14%)
- `packaging_type`: `jar | bottle | box | bag | can | pouch | gift_box`
- `badges`: stored `ArrayField` of `NEW | BEST_SELLER | SALE | LIMITED | ORGANIC | FEATURED`
- `food_metadata` / `honey_metadata`: **flexible `JSONField`s**, populated only for relevant products — there is no fixed schema in the database; food and honey products can carry arbitrary structured attributes (e.g. `{"organic": true, "origin": "Sinai"}`) without a migration. `filter_organic`/`filter_origin` in `ProductFilter` read `honey_metadata.organic`/`origin` or `food_metadata.organic`/`country_of_origin`.
- `weight_grams`: net weight, used for weight-based shipping rates

### Computed badges

`ProductListSerializer.get_computed_badges()` appends badges that are **never stored**, calculated live from inventory/pricing each time the product is serialized:
- `OUT_OF_STOCK` — every active variant has `is_out_of_stock`
- `LOW_STOCK` — any active variant has `is_low_stock` (and not all are out of stock)
- `SALE` — `compare_at_price` is set and `price < compare_at_price`

The response field `computed_badges` is `badges` (stored) + these computed ones combined; `badges` alone reflects only the stored/admin-curated set.

### Variants

`ProductVariant` is the actual purchasable SKU (price, stock, attributes like weight). A `variable` product without explicit variants still works via its own base `price`; simple products typically have zero or one default variant. Each variant's `stock` block (`ProductVariantSerializer.get_stock`) is `{"available_quantity", "is_low_stock", "is_out_of_stock"}`, computed via `apps.inventory.services.get_or_create_item`.

## GET /api/v1/products/

Auth: none. Paginated (`docs/api/pagination.md`). Search fields: `name_en, name_ar, sku, barcode, description` (via `?search=`). Ordering: `?ordering=price|-price|created_at|-created_at|name_en|-name_en` (default `-created_at`).

Query params (`ProductFilter`, `apps/catalog/filters.py`):

| Param | Type | Behavior |
|---|---|---|
| `category` | string | category slug (`category__slug`) |
| `brand` | string | brand slug (`brand__slug`) |
| `min_price` / `max_price` | decimal | `price__gte` / `price__lte` |
| `organic` | bool | `honey_metadata.organic=True` OR `food_metadata.organic=True` |
| `origin` | string | `honey_metadata.origin` OR `food_metadata.country_of_origin`, case-insensitive |
| `attribute` | string | format `"code:value_slug"`, e.g. `weight:500g` — matches product- or variant-level attribute values |
| `on_sale` | bool | `compare_at_price` set and `price < compare_at_price` |
| `featured` | bool | `featured=True` |
| `product_type` | string | `simple\|variable\|bundle\|mix_and_match\|subscription` |
| `search` | string | full-text-ish `icontains` search (DRF `SearchFilter`) |
| `ordering` | string | see above |
| `page` / `page_size` | int | pagination |

Example: `GET /api/v1/products/?category=honey&organic=true&min_price=100&max_price=500&ordering=-price&page=1`

Success `200` (paginated envelope, `ProductListSerializer`):
```json
{
  "success": true,
  "message": "Success",
  "data": {
    "results": [
      {
        "id": 101, "name_en": "Sidr Honey", "name_ar": "عسل سدر", "slug": "sidr-honey",
        "short_description": "Pure raw Sidr honey from Sinai.",
        "brand": { "id": 3, "name": "Alreef", "slug": "alreef", "description": "", "logo": null, "website": "", "country_of_origin": "EG" },
        "category": { "id": 5, "name_en": "Honey", "name_ar": "عسل", "slug": "honey", "image": null, "icon": "", "parent": null },
        "price": "450.00", "compare_at_price": "500.00", "product_type": "variable",
        "badges": ["ORGANIC"], "computed_badges": ["ORGANIC", "SALE"],
        "primary_image": { "id": 10, "image": "/media/products/101/images/main.jpg", "alt_text": "Sidr Honey jar", "is_primary": true, "sort_order": 0, "variant": null },
        "in_stock": true, "featured": true
      }
    ],
    "count": 48, "num_pages": 3, "current_page": 1, "page_size": 20,
    "next": "http://api.example.com/api/v1/products/?page=2", "previous": null
  },
  "errors": null
}
```

Note: `price`/`compare_at_price` serialize as plain decimal strings via `djmoney`'s DRF integration (currency is implicitly EGP unless multi-currency is enabled at the account level).

## GET /api/v1/products/{slug}/

Auth: none. Also records a `RecentlyViewed` entry for the current session/user (see below). `ProductDetailSerializer` adds: `sku, barcode, description, tax_category, weight_grams, packaging_type, food_metadata, honey_metadata, images[], videos[], variants[], attributes[], bundle_items[], seo_title, seo_description` on top of the list fields.

Success `200` (abridged):
```json
{
  "success": true, "message": "Success",
  "data": {
    "id": 101, "name_en": "Sidr Honey", "name_ar": "عسل سدر", "slug": "sidr-honey",
    "sku": "HNY-SDR-001", "barcode": "6221031234567",
    "description": "Raw, unprocessed Sidr honey harvested in South Sinai.",
    "short_description": "Pure raw Sidr honey from Sinai.",
    "brand": { "...": "BrandSerializer" }, "category": { "...": "CategoryListSerializer" },
    "product_type": "variable", "price": "450.00", "compare_at_price": "500.00",
    "tax_category": "standard", "weight_grams": 500, "packaging_type": "jar",
    "badges": ["ORGANIC"], "computed_badges": ["ORGANIC", "SALE"],
    "food_metadata": {},
    "honey_metadata": { "origin": "Sinai", "organic": true, "harvest_season": "spring", "floral_source": "Sidr tree" },
    "images": [ { "id": 10, "image": "/media/products/101/images/main.jpg", "alt_text": "", "is_primary": true, "sort_order": 0, "variant": null } ],
    "videos": [],
    "variants": [
      {
        "id": 501, "sku": "HNY-SDR-001-500G", "barcode": "", "name_suffix": "500g",
        "price": "450.00", "compare_at_price": "500.00", "weight_grams": 500, "is_default": true,
        "attributes": [ { "id": 9, "value_en": "500g", "value_ar": "500 جرام", "slug": "500g" } ],
        "images": [], "stock": { "available_quantity": 42, "is_low_stock": false, "is_out_of_stock": false }
      }
    ],
    "attributes": [ { "id": 4, "value_en": "Sinai", "value_ar": "سيناء", "slug": "sinai" } ],
    "bundle_items": [], "seo_title": "", "seo_description": ""
  },
  "errors": null
}
```

Errors: `NOT_FOUND` (404, DRF default — no product-specific `PRODUCT_NOT_FOUND` is raised here since the queryset itself filters to published/active and `get_object_or_404` semantics apply).

## GET /api/v1/products/{slug}/related/

Auth: none. Rule-based (not ML): same `category`, excludes self, `featured` first then newest, capped at 12. Returns a plain array (not paginated) of `ProductListSerializer` objects.

Errors: `PRODUCT_NOT_FOUND` (404) if the base product doesn't exist/isn't published.

## GET /api/v1/products/recently-viewed/

Auth: none (works for both logged-in users and anonymous sessions via `RecentlyViewed.session_key`). Returns up to 20 most-recently-viewed published products, most recent first, as a plain array of `ProductListSerializer` objects. History is tracked automatically by `GET /products/{slug}/` and trimmed to 20 entries per user/session.

## GET /api/v1/search/?q=

Auth: none. Throttle: `search`. Postgres full-text search (`SearchVector`/`SearchRank`) across `name_en` (weight A), `name_ar` (A), `sku`/`barcode` (B), `brand__name`/`category__name_en` (C), `description` (D). Supports the same `ProductFilter` query params as `/products/` plus `?ordering=price|-price|created_at|-created_at|rank|-rank` (default: relevance/`-rank`).

`q` is **required** — omitting it raises `VALIDATION_ERROR` (400).

Example: `GET /api/v1/search/?q=honey&category=honey&min_price=100`

Response shape: identical paginated envelope as `/products/`, using `ProductListSerializer`.

## Brands — `/api/v1/brands/`

- `GET /api/v1/brands/` — paginated list, active brands only, `?search=name`. `BrandSerializer` fields: `id, name, slug, description, logo, website, country_of_origin`.
- `GET /api/v1/brands/{slug}/` — detail, same fields, wrapped in the non-paginated success envelope.

## Attributes — `/api/v1/attributes/`

- `GET /api/v1/attributes/` — not paginated. Only attributes with `is_filterable=True`. `AttributeSerializer`: `id, name_en, name_ar, code, input_type, is_variant_attribute, is_filterable, unit, values[]`; each `values[]` entry (`AttributeValueSerializer`): `id, value_en, value_ar, slug`.

Example:
```json
{
  "success": true, "message": "Success",
  "data": [
    {
      "id": 2, "name_en": "Weight", "name_ar": "الوزن", "code": "weight", "input_type": "select",
      "is_variant_attribute": true, "is_filterable": true, "unit": "g",
      "values": [ { "id": 9, "value_en": "500g", "value_ar": "500 جرام", "slug": "500g" } ]
    }
  ],
  "errors": null
}
```

Use `code` + a value's `slug` together as the `attribute` filter on `/products/` and `/search/`, e.g. `?attribute=weight:500g`.
