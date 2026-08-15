# Errors

Source: `apps/core/error_codes.py`, `apps/core/response.py`, `apps/core/exceptions.py`.

## Envelope

Every API response — success or failure — has the same top-level shape:

```json
{ "success": true|false, "message": "string", "data": ..., "errors": ... }
```

`apps/core/exceptions.py:custom_exception_handler` is installed as DRF's global `EXCEPTION_HANDLER`, so **every** exception raised anywhere in a view (business errors, DRF validation errors, auth/permission/throttle errors, 404s, unhandled 500s) is funneled through it and normalized to this envelope. There is no code path in this API that returns a raw DRF/Django error body.

## Error shape: `errors.code` + `errors.fields`

On failure, `data` is always `null` and `errors` is always an object of the same two-key shape:

```json
{ "code": "VALIDATION_ERROR", "fields": { "email": ["This field is required."] } }
```

- **`code`** — a machine-readable string from the table below. Always present.
- **`fields`** — a field-name-keyed dict of per-field error message arrays (DRF's native validation error shape) when the failure is a serializer validation error; an **empty object `{}`** for single/business errors that aren't tied to a specific field (e.g. "order not found", "coupon expired").

There is only one shape in practice, not two — `apps/core/response.py:error_response()` supports an older "raw errors dict, no code" mode, but every call site in the codebase (`exceptions.py`, `idempotency.py`) always passes `code`, so `errors` is always `{code, fields}`.

### Business error example

Raised via `raise ApplicationError(ErrorCodes.ORDER_CANNOT_CANCEL, "Order in status 'shipped' cannot be cancelled.")`:

```json
{
  "success": false,
  "message": "Order in status 'shipped' cannot be cancelled.",
  "data": null,
  "errors": { "code": "ORDER_CANNOT_CANCEL", "fields": {} }
}
```
HTTP status: `409` (from `ERROR_STATUS_MAP`).

### Validation error example

A DRF `serializers.ValidationError` (missing/invalid field) is wrapped with `code=VALIDATION_ERROR` and `fields=<DRF's per-field error dict>`:

```json
{
  "success": false,
  "message": "Validation failed.",
  "data": null,
  "errors": { "code": "VALIDATION_ERROR", "fields": { "quantity": ["Ensure this value is greater than or equal to 0."] } }
}
```
HTTP status: `400`.

## Error code reference

| Code | HTTP | Fires when |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Serializer field validation failed, or a business rule needs a generic 400 (e.g. missing search query) |
| `NOT_FOUND` | 404 | Generic DRF 404 / `Http404` (no more specific `*_NOT_FOUND` code applies) |
| `PERMISSION_DENIED` | 403 | DRF `PermissionDenied` — caller authenticated but not allowed |
| `THROTTLED` | 429 | Rate limit exceeded for the endpoint's throttle scope |
| `INTERNAL_ERROR` | 500 | Unhandled server exception (logged; message is a generic "unexpected error" string, never leaks internals) |
| `METHOD_NOT_ALLOWED` | 405 | HTTP verb not supported on the endpoint |
| `IDEMPOTENCY_KEY_CONFLICT` | 409 | `Idempotency-Key` reused with a different body, or a duplicate is currently in-flight |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | Endpoint requires `Idempotency-Key` and it was omitted |
| `AUTH_INVALID_CREDENTIALS` | 401 | Wrong email/password on login, or wrong old password on change-password |
| `AUTH_TOKEN_EXPIRED` | 401 | Refresh token expired |
| `AUTH_TOKEN_INVALID` | 401 | Malformed/blacklisted/already-used token |
| `AUTH_ACCOUNT_DISABLED` | 403 | `User.is_active=False` |
| `AUTH_EMAIL_NOT_VERIFIED` | 403 | Reserved for flows that require a verified email (not currently enforced at login) |
| `AUTH_EMAIL_ALREADY_EXISTS` | 409 | Registration email already taken |
| `AUTH_PHONE_ALREADY_EXISTS` | 409 | Registration phone already taken |
| `AUTH_OTP_INVALID` | 400 | Wrong phone verification code |
| `AUTH_OTP_EXPIRED` | 400 | Phone verification code expired |
| `AUTH_ACCOUNT_LOCKED` | 429 | 10 failed login attempts on this email within 15 minutes — locked out for 15 minutes regardless of caller IP |
| `PRODUCT_NOT_FOUND` | 404 | Product slug not found/not published (used by related-products lookup) |
| `PRODUCT_NOT_PUBLISHED` | 404 | Reserved for explicit unpublished-product checks |
| `PRODUCT_OUT_OF_STOCK` | 409 | Reserved for stock-aware product checks |
| `VARIANT_NOT_FOUND` | 404 | Cart add-item with an unknown/inactive `variant_id` |
| `INSUFFICIENT_STOCK` | 409 | Stock reservation at checkout couldn't be satisfied |
| `RESERVATION_EXPIRED` | 409 | Stock reservation TTL passed before consumption |
| `NO_VALID_BATCH` | 409 | Inventory batch/lot logic found no usable batch |
| `CART_EMPTY` | 400 | Checkout summary/create attempted on an empty cart |
| `CART_ITEM_NOT_FOUND` | 404 | Update/remove referenced a cart item not in this cart |
| `CHECKOUT_SESSION_EXPIRED` | 410 | Reserved for time-boxed checkout sessions |
| `ADDRESS_INVALID` | 400 | Reserved for address-specific validation |
| `ADDRESS_NOT_FOUND` | 404 | `address_id` at checkout doesn't belong to the caller |
| `SHIPPING_UNAVAILABLE` | 409 | Unknown/inactive governorate, or no rate configured |
| `SHIPPING_METHOD_NOT_FOUND` | 404 | Chosen `shipping_method_id` not available for the address |
| `INVALID_COUPON` | 400 | Coupon code doesn't exist/isn't active |
| `COUPON_EXPIRED` | 400 | Coupon past its validity window |
| `COUPON_USAGE_LIMIT_REACHED` | 409 | Coupon's usage cap already hit |
| `COUPON_MINIMUM_NOT_MET` | 400 | Cart subtotal below the coupon's minimum |
| `COUPON_NOT_APPLICABLE` | 400 | Coupon doesn't apply to the cart's contents |
| `ORDER_NOT_FOUND` | 404 | `order_number` not found/not owned by caller |
| `ORDER_CANNOT_CANCEL` | 409 | Order's current status doesn't allow `cancelled` as a next state |
| `ORDER_INVALID_TRANSITION` | 409 | Any other disallowed status transition (incl. return-request preconditions) |
| `PAYMENT_FAILED` | 402 | Provider rejected payment creation |
| `PAYMENT_EXPIRED` | 410 | Payment session/link expired |
| `PAYMENT_AMOUNT_MISMATCH` | 409 | Webhook-reported amount disagrees with the stored payment |
| `PAYMENT_PROVIDER_UNAVAILABLE` | 503 | Provider disabled (e.g. COD off) or its adapter raised `NotImplementedError` (Paymob/Fawry/valU today) |
| `PAYMENT_ALREADY_CAPTURED` | 409 | Attempted to capture a payment not in `pending`/`authorized` |
| `PAYMENT_WEBHOOK_INVALID_SIGNATURE` | 400 | Webhook signature verification failed |
| `REFUND_EXCEEDS_CAPTURED_AMOUNT` | 409 | Requested refund + prior refunds exceeds the captured amount |
| `GIFT_CARD_INVALID` | 400 | Gift card code unknown/inactive |
| `GIFT_CARD_EXPIRED` | 400 | Gift card past expiry |
| `GIFT_CARD_INSUFFICIENT_BALANCE` | 409 | Gift card balance too low for the requested use |
| `LOYALTY_INSUFFICIENT_POINTS` | 409 | Not enough loyalty points for the requested redemption |
| `REVIEW_NOT_ELIGIBLE` | 403 | Caller hasn't purchased/received the product being reviewed |
| `REVIEW_ALREADY_EXISTS` | 409 | Caller already reviewed this product |

`ApplicationError(code, message=None, details=None)` is the standard way service-layer code raises a business error; `details` (if given) is passed through as `errors.fields`. If `message` is omitted it defaults to the code humanized (e.g. `PRODUCT_NOT_FOUND` → "Product not found").
