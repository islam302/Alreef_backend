# Categories

Base path: `/api/v1/categories/`

Source: `apps/categories/{urls,views,serializers,models}.py`. All endpoints are `AllowAny` (public, read-only).

## Model

`Category` (`apps/categories/models.py`) is an `MPTTModel` (django-mptt) — a materialized hierarchical tree (Honey / Food / Grocery / Beverages / ...). Fields: `name_en, name_ar, slug, description, parent, image, icon, is_active, sort_order, seo_title, seo_description`. Ordering within a level follows `sort_order, name_en` (`MPTTMeta.order_insertion_by`).

## GET /api/v1/categories/

Auth: none. **Not paginated** — returns the full flat list of active categories as a plain array in `data`. Supports `?parent=<id>` to filter to direct children of a given category, and `?search=` over `name_en`/`name_ar`.

`CategoryListSerializer` fields: `id, name_en, name_ar, slug, image, icon, parent`.

```json
{
  "success": true, "message": "Success",
  "data": [
    { "id": 5, "name_en": "Honey", "name_ar": "عسل", "slug": "honey", "image": "/media/categories/honey/hero.jpg", "icon": "honey-jar", "parent": null },
    { "id": 6, "name_en": "Sidr Honey", "name_ar": "عسل سدر", "slug": "sidr-honey", "image": null, "icon": "", "parent": 5 }
  ],
  "errors": null
}
```

## GET /api/v1/categories/tree/

Auth: none. Returns only **root** categories (`parent__isnull=True`), each recursively nesting active `children` — the full tree in one call. Ordered `sort_order, name_en` at every level.

`CategoryTreeSerializer` fields: `id, name_en, name_ar, slug, image, icon, children` (recursive).

```json
{
  "success": true, "message": "Success",
  "data": [
    {
      "id": 5, "name_en": "Honey", "name_ar": "عسل", "slug": "honey", "image": "/media/categories/honey/hero.jpg", "icon": "honey-jar",
      "children": [
        { "id": 6, "name_en": "Sidr Honey", "name_ar": "عسل سدر", "slug": "sidr-honey", "image": null, "icon": "", "children": [] },
        { "id": 7, "name_en": "Wildflower Honey", "name_ar": "عسل بري", "slug": "wildflower-honey", "image": null, "icon": "", "children": [] }
      ]
    },
    { "id": 8, "name_en": "Grocery", "name_ar": "بقالة", "slug": "grocery", "image": null, "icon": "", "children": [] }
  ],
  "errors": null
}
```

## GET /api/v1/categories/{slug}/

Auth: none. `CategoryDetailSerializer` fields: `id, name_en, name_ar, slug, description, parent, image, icon, seo_title, seo_description, breadcrumbs`.

`breadcrumbs` is computed via MPTT's `get_ancestors(include_self=True)` — the full path from root to this category, root first:

```json
{
  "success": true, "message": "Success",
  "data": {
    "id": 6, "name_en": "Sidr Honey", "name_ar": "عسل سدر", "slug": "sidr-honey",
    "description": "Single-origin Sidr honey varieties.",
    "parent": 5, "image": null, "icon": "",
    "seo_title": "", "seo_description": "",
    "breadcrumbs": [
      { "id": 5, "name_en": "Honey", "slug": "honey" },
      { "id": 6, "name_en": "Sidr Honey", "slug": "sidr-honey" }
    ]
  },
  "errors": null
}
```

Errors: `NOT_FOUND` (404, unknown or inactive slug — the queryset is filtered to `is_active=True`, so DRF's default 404 handling applies).

## Using categories with products

`GET /api/v1/products/?category=<slug>` filters by `category__slug` — pass the leaf category's slug, not the id. There is no automatic inclusion of descendant categories' products in `/products/` — the filter only matches the exact category.
