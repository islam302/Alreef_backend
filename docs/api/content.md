# Site content

Base paths: `/api/v1/content/` (public) and `/api/v1/admin-api/` (staff).

Source: `apps/content/{models,serializers,views,views_admin,urls,urls_admin}.py`.

Everything the storefront says about the business but that is not a product lives here: the identity, the phones and branches, the social profiles, the static pages, the FAQ, and the messages the contact form produces. It used to be hardcoded in the storefront bundle, so a new phone number needed a redeploy.

Seed the tables from the storefront's confirmed copy with:

```bash
python manage.py seed_content     # idempotent
```

## Models

| Model | What it holds |
|---|---|
| `SiteSettings` | Singleton (pk=1): names, tagline, founding year, logo/OG image, contact email, announcement bar, default SEO. Read it with `SiteSettings.load()` — the row is created on first access. |
| `ContactPhone` | `label, display, dial, is_whatsapp, is_primary, sort_order, is_active`. `dial` is digits-only with the country code (`201554112325`) and drives `tel:`/`wa.me`. At most one row is `is_primary`; promoting one demotes the other in the same transaction. |
| `Branch` | `name, address, region, plus_code, postal_code, maps_url, phone, working_hours_ar, sort_order, is_active`. |
| `SocialLink` | `platform` (facebook/instagram/tiktok/youtube/whatsapp/x/linkedin/other), `label, url, sort_order, is_active`. |
| `Page` | `slug, title_ar, title_en, subtitle_ar, body_ar, body_en, hero_image, hero_image_alt, meta_title, meta_description, is_published, sort_order`. |
| `PageSection` | Ordered blocks inside a page: `kind` (text/card/stat/image/quote/cta), `key, eyebrow, title, body, image, image_alt, icon, link_url, link_label, data (JSON), sort_order, is_active`. |
| `FaqItem` | `question, answer, topic, sort_order, is_active`. |
| `ContactMessage` | `name, phone, email, subject, message` plus staff triage: `status` (new/read/replied/archived/spam), `staff_note, handled_by, handled_at, ip_address`. |

**Copy is plain text, not HTML.** `body_ar`, `body_en`, section bodies, FAQ answers and the announcement text are validated by `apps.core.validators.validate_no_unsafe_html`, which rejects tags such as `<script>`/`<iframe>`, inline `on…=` handlers and `javascript:` URLs. The storefront renders them as text — never with `dangerouslySetInnerHTML`.

---

# Public endpoints

All `AllowAny`. Reads are unpaginated (the lists are small and the page needs all of them at once).

## GET /api/v1/content/site/

Identity, phones, branches and social links in one payload — the header, the footer and every SEO block come from it, so fetch it once and cache it client-side. Inactive rows are omitted. On an empty database it answers with blank values rather than 404.

```json
{
  "success": true, "message": "Success",
  "data": {
    "settings": {
      "name_ar": "الريف", "full_name_ar": "عسل نحل الريف", "name_en": "El Rif",
      "legal_name_ar": "شركة الريف للمواد الغذائية",
      "descriptor_ar": "عسل ومنتجات النحل", "descriptor_en": "Honey & bee products",
      "tagline_ar": "الريف، على سفرتك", "tagline_en": "El Rif, at your table",
      "founded_year": null, "site_url": "https://alreef-honey.com",
      "logo": null, "og_image": null, "email": "", "support_hours_ar": "",
      "announcement_enabled": false, "announcement_text_ar": "", "announcement_url": "",
      "meta_title_ar": "…", "meta_description_ar": "…",
      "updated_at": "2026-09-20T06:32:57.895741+03:00"
    },
    "phones": [
      { "id": 1, "label": "", "display": "+20 15 54112325", "dial": "201554112325",
        "is_whatsapp": true, "is_primary": true, "sort_order": 0 }
    ],
    "branches": [
      { "id": 1, "name": "الفرع الرئيسي", "address": "طريق طنطا المحلة أمام محطة قطار حصة شبشير",
        "region": "الغربية", "plus_code": "V35C+RPF", "postal_code": "6647775",
        "maps_url": "https://maps.google.com/?q=…", "phone": "", "working_hours_ar": "", "sort_order": 0 }
    ],
    "social": [
      { "id": 1, "platform": "facebook", "label": "فيسبوك", "url": "https://www.facebook.com/AlreeForHony/", "sort_order": 0 }
    ]
  },
  "errors": null
}
```

## GET /api/v1/content/pages/

Published pages only: `id, slug, title_ar, title_en, subtitle_ar, sort_order, updated_at`. For the footer nav and the sitemap.

## GET /api/v1/content/pages/{slug}/

One published page with its **active** sections in `sort_order`. 404 when the page is unpublished or missing.

```json
{
  "success": true, "message": "Success",
  "data": {
    "id": 1, "slug": "about", "title_ar": "عن الريف", "title_en": "About",
    "subtitle_ar": "الريف، على سفرتك", "body_ar": "…", "body_en": "",
    "hero_image": null, "hero_image_alt": "",
    "meta_title": "…", "meta_description": "…",
    "sections": [
      { "id": 2, "kind": "card", "key": "value-clarity", "eyebrow": "", "title": "الوضوح",
        "body": "…", "image": null, "image_alt": "", "icon": "information",
        "link_url": "", "link_label": "", "data": {}, "sort_order": 1 }
    ],
    "updated_at": "2026-09-20T06:32:57.951+03:00"
  },
  "errors": null
}
```

## GET /api/v1/content/faqs/

Active FAQ items in display order: `id, question, answer, topic, sort_order`. Optional `?topic=` filter. Unpaginated, so the page can also emit `FAQPage` structured data from the same list.

## POST /api/v1/content/contact-messages/

Anonymous, throttle scope `contact` (**5/min**). Accepts `name, phone, email?, subject?, message`.

- `phone` must be an Egyptian mobile (`01[0125]` + 8 digits); spaces and dashes are stripped before validation.
- `name` needs 3+ characters, `message` needs 10+.
- `status`, `staff_note`, `handled_by` are **not** writable here — a visitor cannot file a message as already replied.

Returns `201` with only an acknowledgement, so the endpoint can never be used to read other rows:

```json
{ "success": true, "message": "Message received.",
  "data": { "id": 12, "created_at": "2026-09-20T07:10:04+03:00" }, "errors": null }
```

---

# Staff endpoints

All under `/api/v1/admin-api/`, all `IsAdminUser`, all in the standard envelope, and every mutation writes an `AuditLog` row. Lists here include inactive/unpublished rows and are unpaginated except the message inbox.

| Method | Path | Notes |
|---|---|---|
| `GET` / `PATCH` | `site-settings/` | Singleton — no list, no create, no delete. Accepts `multipart/form-data` for `logo` / `og_image`. |
| `GET POST PATCH DELETE` | `contact-phones/` | |
| `GET POST PATCH DELETE` | `branches/` | |
| `GET POST PATCH DELETE` | `social-links/` | |
| `GET POST PATCH DELETE` | `faqs/` | `?search=` over question/answer/topic. |
| `GET POST PATCH DELETE` | `pages/` | Detail accepts an id **or** a slug: `pages/about/`. Detail nests `sections`; list adds `section_count`. Accepts `multipart/form-data` for `hero_image`. |
| `POST` | `pages/{id}/sections/` | Create a section on that page. |
| `GET POST PATCH DELETE` | `page-sections/` | `?page=<id or slug>` to scope the list. Accepts `multipart/form-data` for `image`. |
| `GET PATCH DELETE` | `contact-messages/` | Paginated. `?status=new,read` and `?search=`. |
| `GET` | `contact-messages/unread-count/` | `{ "count": 3 }` — drives the sidebar badge. |
| `POST` | `contact-phones\|branches\|social-links\|faqs\|pages\|page-sections/reorder/` | Bulk re-sequence, see below. |

## Reordering

Drag-and-drop should not fire N patches:

```http
POST /api/v1/admin-api/faqs/reorder/
{ "items": [ { "id": 7, "sort_order": 0 }, { "id": 3, "sort_order": 1 } ] }
```

Responds with the full list in its new order.

## Contact messages

Customer-authored fields (`name, phone, email, subject, message, ip_address, created_at`) are **read-only** — staff triage, they never rewrite the record. Writable: `status` and `staff_note`.

Moving a message out of `new` stamps `handled_by` (the staff user making the call) and `handled_at` automatically.

## Uploads

`logo`, `og_image`, `hero_image` and section `image` use the shared image rules in [image-upload.md](image-upload.md): `.jpg/.jpeg/.png/.webp`, 5 MB, 6000×6000 max, verified as a real decodable image. Send `multipart/form-data` and let the browser set the boundary.
