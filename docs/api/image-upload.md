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

## 3. Product / category / brand images

These are catalog/merchandising data, not user-generated content, so they
are uploaded through the **Django admin** (`/admin/`) rather than a public
API endpoint — the admin's image widgets already do multipart upload with
the same validators. If a future storefront CMS integration needs
programmatic catalog image upload, add an admin-only `multipart/form-data`
endpoint following the same pattern as the review upload above; none exists
today because nothing in this build currently calls it.

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
