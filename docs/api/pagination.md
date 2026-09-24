# Pagination

Source: `apps/core/pagination.py:StandardResultsPagination`, set as the DRF-wide default (`DEFAULT_PAGINATION_CLASS` in `config/settings/base.py`).

Standard `PageNumberPagination`, applied automatically to any `ListAPIView`/list action that doesn't explicitly opt out (`pagination_class = None`) — see individual doc files for which list endpoints are paginated vs. not (e.g. categories and attributes return plain arrays; products, brands, orders, search do not).

## Query params

| Param | Default | Max | Notes |
|---|---|---|---|
| `page` | 1 | — | 1-indexed page number |
| `page_size` | 20 | 100 | Requests above 100 are silently capped at 100, not rejected |

Example: `GET /api/v1/products/?page=2&page_size=50`

## Response envelope

Paginated endpoints nest the page metadata **inside** the standard `data` field rather than at the top level:

```json
{
  "success": true,
  "message": "Success",
  "data": {
    "results": [ /* array of serialized objects */ ],
    "count": 123,
    "num_pages": 7,
    "current_page": 2,
    "page_size": 20,
    "next": "http://api.example.com/api/v1/products/?page=3",
    "previous": "http://api.example.com/api/v1/products/?page=1"
  },
  "errors": null
}
```

| Field | Type | Meaning |
|---|---|---|
| `data.results` | array | the page of items, in their normal serialized shape |
| `data.count` | int | total number of items across all pages |
| `data.num_pages` | int | total number of pages at the current `page_size` |
| `data.current_page` | int | the page number that was served |
| `data.page_size` | int | the effective page size used (reflects `?page_size=` if given, else the default) |
| `data.next` | string \| null | full URL for the next page, or `null` on the last page |
| `data.previous` | string \| null | full URL for the previous page, or `null` on the first page |

`next`/`previous` are absolute URLs (including the current query string with `page` swapped), ready to use directly as the next request.

Requesting a `page` beyond `num_pages` returns a standard DRF 404 (`NOT_FOUND`, see `docs/api/errors.md`), not an empty `results` array.
