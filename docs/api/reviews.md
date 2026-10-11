# Reviews

## List a product's reviews — `GET /api/v1/products/{product_id}/reviews/`

Public. Only `approved=true` reviews are returned (moderation queue lives in
Django admin). Paginated (see `docs/api/pagination.md`).

```json
{
  "success": true,
  "message": "Success",
  "data": {
    "results": [
      {
        "id": 12,
        "user_name": "Test C.",
        "rating": 5,
        "title": "Excellent honey",
        "comment": "Rich flavor, arrived well packaged.",
        "images": [{ "id": 3, "image": "/media/reviews/12/photo1.jpg" }],
        "verified_purchase": true,
        "helpful_votes": 4,
        "created_at": "2026-08-01T10:15:00Z"
      }
    ],
    "count": 1, "num_pages": 1, "current_page": 1, "page_size": 20, "next": null, "previous": null
  },
  "errors": null
}
```

`user_name` is the reviewer's first name and last initial ("محمد أ."). A
reviewer with no first name is shown as "عميل الريف" — never by email, which
`User.full_name` falls back to.

## Latest reviews — `GET /api/v1/reviews/latest/`

Public. The newest approved reviews **that have written text**, across every
product that is published and active — the homepage's social proof. A plain
list (not paginated), newest first; `?limit=` 1–20, default 8. Each item is
the object above plus the product it is about:

```json
{ "id": 12, "user_name": "محمد أ.", "rating": 5, "comment": "…", "verified_purchase": true,
  "product": { "name_ar": "عسل نحل برسيم حجازي", "name_en": "Alfalfa Honey", "slug": "asal-barseem-hegazy" }, "…": "…" }
```

`verified_purchase` is only ever true for a review written against a
delivered order (see "Create a review" below), so it can be shown as such.

## Create a review — `POST /api/v1/reviews/`

Auth required. **Only customers with a `DELIVERED` order containing this
product may review it** (spec §30) — this is enforced server-side, not just
a UI hint. See `apps/reviews/services.get_eligible_order_item`.

```json
{ "product_id": 1, "rating": 5, "title": "Great!", "comment": "Loved it." }
```

To attach photos, send the same fields as `multipart/form-data` plus up to
5 `images` file parts — see `docs/api/image-upload.md`.

New reviews start with `approved=false` (`verified_purchase=true` is set
automatically since eligibility already proved the purchase) and are hidden
from the public list endpoint until a staff member approves them in
Django admin.

**Errors:**
- `REVIEW_NOT_ELIGIBLE` (403) — no delivered order for this product.
- `REVIEW_ALREADY_EXISTS` (409) — you already reviewed this specific
  delivered order/item.
- `PRODUCT_NOT_FOUND` (404).

## Vote a review helpful — `POST /api/v1/reviews/{review_id}/helpful/`

Auth required. One vote per user per review (idempotent — voting again is a
no-op, `helpful_votes` doesn't double-count).
