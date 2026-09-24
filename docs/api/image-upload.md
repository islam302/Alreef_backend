# Image Upload

## Accepted formats & limits

Enforced by `apps.core.validators.validate_image_file` on every image field:

| Rule | Value |
|---|---|
| Extensions | `.jpg`, `.jpeg`, `.png`, `.webp` |
| MIME types | `image/jpeg`, `image/png`, `image/webp` |
| Max file size | 5 MB |
| Max dimensions | 6000 × 6000 px |

Uploads are validated for a genuine, decodable image (Pillow `Image.verify()`),
not just extension/MIME sniffing.

## 1. Profile picture — `PATCH /api/v1/auth/me/`

Send `multipart/form-data` (not JSON) with a `profile_image` file field.

```bash
curl -X PATCH "{{base_url}}/api/v1/auth/me/" \
  -H "Authorization: Bearer {{access_token}}" \
  -F "profile_image=@avatar.jpg"
```

Response: the updated user object, with `profile_image` as an absolute or
relative media URL (see `MEDIA_URL` — `/media/users/<id>/profile/<filename>`).

## 2. Review photos — `POST /api/v1/reviews/`

Send `multipart/form-data` with the normal review fields (`product_id`,
`rating`, `title`, `comment`) plus up to 5 `images` file parts.

```bash
curl -X POST "{{base_url}}/api/v1/reviews/" \
  -H "Authorization: Bearer {{access_token}}" \
  -F "product_id=1" \
  -F "rating=5" \
  -F "comment=Great honey!" \
  -F "images=@photo1.jpg" \
  -F "images=@photo2.jpg"
```

Images are stored at `/media/reviews/<review_id>/<filename>` and returned in
`data.images[].image` on the created review.

## 3. Product images — admin only

Catalog imagery is merchandising data, not user-generated content, so there
is no public endpoint. Staff upload it through `/api/v1/admin-api/`
(`IsAdminUser`) with `multipart/form-data`:

```bash
# Add an image to a product
curl -X POST "{{base_url}}/api/v1/admin-api/products/12/images/" \
  -H "Authorization: Bearer {{staff_access_token}}" \
  -F "image=@jar.jpg" \
  -F "alt_text=برطمان عسل سدر 500 جم" \
  -F "is_primary=true"

# Edit alt text / primary flag / order, or remove it
curl -X PATCH  "{{base_url}}/api/v1/admin-api/product-images/34/" ...
curl -X DELETE "{{base_url}}/api/v1/admin-api/product-images/34/" ...
```

Setting `is_primary=true` clears the flag on the product's other images.
Django admin (`/admin/`) edits the same rows through the same validators.

## 4. Site content images — admin only

`logo` and `og_image` on `PATCH /api/v1/admin-api/site-settings/`, plus
`hero_image` on pages and `image` on page sections. Same rules, same
multipart form. See [content.md](content.md).

## 5. Category / brand images

No API endpoint yet — these are edited in Django admin, whose image widgets
run the same validators.

## Frontend example (fetch)

```typescript
async function uploadProfileImage(file: File, accessToken: string) {
  const formData = new FormData();
  formData.append("profile_image", file);

  const response = await fetch(`${API_URL}/auth/me/`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${accessToken}` }, // no Content-Type — browser sets the multipart boundary
    body: formData,
  });
  return response.json();
}
```

## Errors

Validation failures return the standard envelope with
`errors.code = "VALIDATION_ERROR"` and a field-level message, e.g.
`"Image too large (7340032 bytes). Maximum is 5242880 bytes."`.
