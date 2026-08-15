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
        "user_name": "Test Customer",
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
