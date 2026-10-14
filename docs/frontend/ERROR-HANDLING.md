# Error handling

Everything the frontend needs to turn a failed API call into the right UI: what the
envelope looks like, which codes exist, which are retryable, and where to put the
message. Codes come from `apps/core/error_codes.py`; the HTTP status for each comes from
`ERROR_STATUS_MAP` in the same file.

## The error envelope

```jsonc
{
  "success": false,
  "message": "Invalid email or password.",
  "data": null,
  "errors": {
    "code": "AUTH_INVALID_CREDENTIALS",
    "fields": {}
  }
}
```

- `errors.code` — branch on this, never on `message`. Message text is not stable and
  will change once it is translated.
- `errors.fields` — DRF's `{ field_name: ["message", ...] }` shape, populated only for
  `VALIDATION_ERROR`. It is `{}` for single business-rule errors.
- `message` — safe to display as a fallback, but prefer your own copy keyed on the code
  for anything user-critical (auth, checkout, payment).

`errors.fields` can also carry DRF's `non_field_errors` key for form-level validation
that isn't tied to a single input. Render that above the form, not next to a field.

## Read it once, in one place

```ts
export interface ApiError {
  status: number;
  code: string;
  message: string;
  fields: Record<string, string[]>;
}

export function toApiError(err: unknown): ApiError {
  const res = (err as any)?.response;
  const body = res?.data;
  return {
    status: res?.status ?? 0,
    code: body?.errors?.code ?? (res ? "INTERNAL_ERROR" : "NETWORK_ERROR"),
    message: body?.message ?? "Something went wrong. Please try again.",
    fields: body?.errors?.fields ?? {},
  };
}
```

`status: 0` / `code: "NETWORK_ERROR"` is the case with no response at all — offline, DNS
failure, CORS rejection, request cancelled. It is not something the backend can produce,
but it is the one your users will hit most on mobile data, so give it real copy.

## The 401 rule (read this before writing the auth interceptor)

**Refresh on HTTP 401 status, not on the error code.**

An expired or malformed *access* token is rejected by SimpleJWT's authentication class,
which raises `InvalidToken`. That exception type is not in `_DRF_CODE_MAP`
(`apps/core/exceptions.py`), so the handler falls through to its default and the
response looks like this:

```jsonc
// GET /api/v1/auth/me/ with an expired access token  ->  HTTP 401
{
  "success": false,
  "message": "Given token not valid for any token type",
  "data": null,
  "errors": { "code": "VALIDATION_ERROR", "fields": {} }
}
```

`AUTH_TOKEN_EXPIRED` and `AUTH_TOKEN_INVALID` are raised in only two places —
`POST /auth/token/refresh/` and `POST /auth/logout/`, both of which validate a *refresh*
token explicitly. A client that only refreshes when it sees those two codes will never
refresh on a normal expired-access-token 401 and will log the user out every 15 minutes.

So the condition is:

```ts
const shouldRefresh =
  error.response?.status === 401 &&
  !original._retried &&
  !original.url?.includes("/auth/token/refresh/"); // never refresh the refresh call
```

Which 401s mean what, in practice:

| Situation | Status | `errors.code` |
|---|---|---|
| Expired / malformed access token | 401 | `VALIDATION_ERROR` |
| No `Authorization` header at all | 401 | `AUTH_TOKEN_INVALID` |
| Wrong email or password at login | 401 | `AUTH_INVALID_CREDENTIALS` |
| Refresh token expired or blacklisted | 401 | `AUTH_TOKEN_EXPIRED` |

Only the last one means "the session is truly over" — clear tokens and send the user to
login. `AUTH_INVALID_CREDENTIALS` on `/auth/login/` is a form error, not a session
expiry; make sure your interceptor doesn't treat it as one and bounce the user off the
login page they're standing on.

> The client in [`../../frontend-sdk/api-client.ts`](../../frontend-sdk/api-client.ts)
> still uses the narrower code-based condition at line 174. Widen it to a plain status
> check before relying on silent refresh.

## Full code reference

Grouped by what the frontend should *do*, not by domain.

### Retry silently (the user should never see these)

| Code | HTTP | Action |
|---|---|---|
| `VALIDATION_ERROR` **on a 401** | 401 | Refresh the access token, replay the request once. |
| `IDEMPOTENCY_KEY_CONFLICT` *(concurrent duplicate)* | 409 | A request with the same key is mid-flight. Wait ~1s and retry with the **same** key. |
| `THROTTLED` | 429 | Back off and retry once after a short delay. Read the `Retry-After` header if present. |

### Show a form error next to the input

| Code | HTTP | Field |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Map each key in `errors.fields` to its input. |
| `AUTH_INVALID_CREDENTIALS` | 401 | Email/password pair, or the "current password" input on change-password. |
| `AUTH_EMAIL_ALREADY_EXISTS` | 409 | Email field on the register form. |
| `AUTH_PHONE_ALREADY_EXISTS` | 409 | Phone field on the register form. |
| `AUTH_OTP_INVALID` | 400 | OTP input — let them retype, don't resend automatically. |
| `AUTH_OTP_EXPIRED` | 400 | OTP input, plus surface the "resend code" button. |
| `INVALID_COUPON` | 400 | Coupon input. |
| `COUPON_EXPIRED` | 400 | Coupon input. |
| `COUPON_MINIMUM_NOT_MET` | 400 | Coupon input — the message names the minimum; show it. |
| `COUPON_NOT_APPLICABLE` | 400 | Coupon input — nothing in this cart qualifies. |
| `COUPON_USAGE_LIMIT_REACHED` | 409 | Coupon input. |
| `GIFT_CARD_INVALID` | 400 | Gift card code input. |
| `GIFT_CARD_EXPIRED` | 400 | Gift card code input. |
| `ADDRESS_INVALID` | 400 | Address form — check `errors.fields` for which part. |

### Show a blocking message and change what's on screen

| Code | HTTP | What it means / what to do |
|---|---|---|
| `CART_EMPTY` | 400 | Checkout with nothing in the cart. Send them back to the cart page. |
| `CART_ITEM_NOT_FOUND` | 404 | The line was already removed elsewhere. Refetch the cart. |
| `PRODUCT_OUT_OF_STOCK` | 409 | Refetch the product; disable add-to-cart. |
| `INSUFFICIENT_STOCK` | 409 | Not enough units to reserve. Refetch the cart and show the available quantity. |
| `RESERVATION_EXPIRED` | 409 | The stock hold timed out during a slow checkout. Restart checkout from the cart. |
| `NO_VALID_BATCH` | 409 | Nothing sellable left (expiry/FEFO). Treat as out of stock. |
| `CHECKOUT_SESSION_EXPIRED` | 410 | Restart checkout. |
| `ADDRESS_NOT_FOUND` | 404 | The selected address was deleted. Refetch addresses, clear the selection. |
| `SHIPPING_UNAVAILABLE` | 409 | No rate for that governorate. Ask them to pick another, or offer pickup. |
| `SHIPPING_METHOD_NOT_FOUND` | 404 | Refetch quotes — the method was deactivated. |
| `ORDER_CANNOT_CANCEL` | 409 | Too late to cancel. Refetch the order; hide the cancel button. |
| `ORDER_PAYMENT_IN_PROGRESS` | 409 | A card/wallet payment is still unconfirmed. Ask the customer to wait; never suggest paying again. |
| `ORDER_ALREADY_PAID` | 409 | Paid online; cancelling means a refund. Point to contact/support. |
| `ORDER_INVALID_TRANSITION` | 409 | Same — the order moved on. Refetch. |
| `LOYALTY_INSUFFICIENT_POINTS` | 409 | Show the real balance from `GET /loyalty/`. |
| `GIFT_CARD_INSUFFICIENT_BALANCE` | 409 | Show the remaining balance. |
| `REVIEW_ALREADY_EXISTS` | 409 | One review per product per order item. Show their existing review instead. |
| `REVIEW_NOT_ELIGIBLE` | 403 | No verified purchase. Hide the review form. |

### Payment-specific

| Code | HTTP | What to do |
|---|---|---|
| `PAYMENT_FAILED` | 402 | The gateway declined. Offer to retry or pick another method — the order exists and is unpaid. |
| `PAYMENT_EXPIRED` | 410 | The payment window closed — a checkout intention that lapsed, or an InstaPay transfer never made. Offer a new attempt. |
| `PAYMENT_PROVIDER_UNAVAILABLE` | 503 | **The most common one today** — that method isn't enabled server-side. See [`PAYMENTS.md`](./PAYMENTS.md); hide the method rather than letting users pick it. |
| `PAYMENT_AMOUNT_MISMATCH` | 409 | Server-side integrity check failed. Don't retry; tell the user to contact support. |
| `PAYMENT_ALREADY_CAPTURED` | 409 | Already paid. Refetch the order and show it as paid. |

### Session / account

| Code | HTTP | What to do |
|---|---|---|
| `AUTH_TOKEN_EXPIRED` | 401 | Only from `/auth/token/refresh/`. The session is over: clear tokens, redirect to login. |
| `AUTH_TOKEN_INVALID` | 401 | No or unusable credentials. Same treatment. |
| `AUTH_ACCOUNT_DISABLED` | 403 | Account deactivated. Dedicated screen — refreshing won't help. |
| `AUTH_ACCOUNT_LOCKED` | **429** | Too many failed logins. Note the status is 429, not 403 — don't let a generic "rate limited, retrying" handler swallow it. Show the real message. |
| `AUTH_EMAIL_NOT_VERIFIED` | 403 | Offer "resend verification email" (`POST /auth/verify-email/`). |
| `PERMISSION_DENIED` | 403 | Authenticated but not allowed. Usually a routing bug on the frontend. |

### Never expected in a correct client

| Code | HTTP | Meaning |
|---|---|---|
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | You forgot the header on `POST /checkout/`. |
| `METHOD_NOT_ALLOWED` | 405 | Wrong verb. |
| `NOT_FOUND`, `PRODUCT_NOT_FOUND`, `PRODUCT_NOT_PUBLISHED`, `VARIANT_NOT_FOUND`, `ORDER_NOT_FOUND` | 404 | Render your 404 page. For a product slug this is normal (stale link); for a variant id it means your cached product data is stale — refetch. |
| `INTERNAL_ERROR` | 500 | Unhandled server exception. Show a generic failure and report it. |
| `PAYMENT_WEBHOOK_INVALID_SIGNATURE` | 400 | Server-to-server only. |
| `REFUND_EXCEEDS_CAPTURED_AMOUNT` | 409 | Admin endpoint only. |

## A message map you can copy

Keyed by code, with both languages, since the storefront ships `ar` first. Fall back to
the envelope's `message` for anything unlisted.

```ts
type Copy = { ar: string; en: string };

export const ERROR_COPY: Record<string, Copy> = {
  NETWORK_ERROR: {
    ar: "تعذّر الاتصال. تحقّق من الإنترنت وحاول مرة أخرى.",
    en: "Couldn't reach the server. Check your connection and try again.",
  },
  INTERNAL_ERROR: {
    ar: "حدث خطأ غير متوقع. حاول مرة أخرى بعد قليل.",
    en: "Something went wrong on our side. Please try again shortly.",
  },
  THROTTLED: {
    ar: "محاولات كثيرة. انتظر قليلاً ثم حاول مرة أخرى.",
    en: "Too many attempts. Please wait a moment and try again.",
  },
  AUTH_INVALID_CREDENTIALS: {
    ar: "البريد الإلكتروني أو كلمة المرور غير صحيحة.",
    en: "Incorrect email or password.",
  },
  AUTH_ACCOUNT_LOCKED: {
    ar: "تم إيقاف تسجيل الدخول مؤقتاً بسبب محاولات فاشلة متكررة.",
    en: "Sign-in is temporarily locked after too many failed attempts.",
  },
  AUTH_EMAIL_ALREADY_EXISTS: {
    ar: "يوجد حساب بهذا البريد الإلكتروني بالفعل.",
    en: "An account with this email already exists.",
  },
  AUTH_OTP_INVALID: { ar: "الرمز غير صحيح.", en: "That code isn't right." },
  AUTH_OTP_EXPIRED: { ar: "انتهت صلاحية الرمز. اطلب رمزاً جديداً.", en: "That code expired. Request a new one." },
  CART_EMPTY: { ar: "سلة التسوق فارغة.", en: "Your cart is empty." },
  INSUFFICIENT_STOCK: {
    ar: "الكمية المطلوبة غير متوفرة حالياً.",
    en: "We don't have that many in stock right now.",
  },
  PRODUCT_OUT_OF_STOCK: { ar: "هذا المنتج غير متوفر حالياً.", en: "This product is out of stock." },
  INVALID_COUPON: { ar: "كود الخصم غير صالح.", en: "That coupon code isn't valid." },
  COUPON_EXPIRED: { ar: "انتهت صلاحية كود الخصم.", en: "That coupon has expired." },
  COUPON_MINIMUM_NOT_MET: {
    ar: "قيمة الطلب أقل من الحد الأدنى لاستخدام هذا الكود.",
    en: "Your order is below the minimum for this coupon.",
  },
  SHIPPING_UNAVAILABLE: {
    ar: "لا يتوفر شحن لهذه المحافظة حالياً.",
    en: "We can't ship to that governorate yet.",
  },
  PAYMENT_FAILED: { ar: "فشلت عملية الدفع. جرّب وسيلة دفع أخرى.", en: "The payment failed. Try another method." },
  PAYMENT_PROVIDER_UNAVAILABLE: {
    ar: "وسيلة الدفع هذه غير متاحة حالياً.",
    en: "That payment method isn't available right now.",
  },
  ORDER_CANNOT_CANCEL: {
    ar: "لا يمكن إلغاء الطلب في هذه المرحلة.",
    en: "This order can no longer be cancelled.",
  },
};

export function messageFor(err: ApiError, lang: "ar" | "en"): string {
  return ERROR_COPY[err.code]?.[lang] ?? err.message;
}
```

The Arabic strings are a starting point, not reviewed copy — get them checked before
launch.

## Validation errors and forms

`errors.fields` keys match the serializer field names, which match the request body
keys — so they map straight onto a react-hook-form / Formik field name:

```ts
function applyServerErrors(err: ApiError, setError: (name: string, e: { message: string }) => void) {
  for (const [field, messages] of Object.entries(err.fields)) {
    if (field === "non_field_errors") continue; // render this one at form level
    setError(field, { message: Array.isArray(messages) ? messages[0] : String(messages) });
  }
}
```

Two shapes to handle: nested serializers produce nested objects
(`{ "profile": { "date_of_birth": ["..."] } }` from `PATCH /auth/me/`), and list fields
produce arrays indexed by position (`{ "items": [{}, { "quantity": ["..."] }] }` from a
return request). Flatten to dotted paths (`profile.date_of_birth`, `items.1.quantity`)
if your form library expects that.

## Retry policy in one table

| Situation | Retry? | Notes |
|---|---|---|
| `NETWORK_ERROR` on a GET | Yes | Exponential backoff, 2-3 attempts. |
| `NETWORK_ERROR` on `POST /checkout/` | Yes | Safe **only** because of the `Idempotency-Key` — reuse the same key. |
| `NETWORK_ERROR` on any other POST | No | Not idempotent. Ask the user, and refetch state first so they can see whether it landed. |
| 401 | Once | Refresh, then replay. Never twice. |
| 429 | Once | After a delay. |
| 4xx other than 401/429 | No | The request is wrong; retrying changes nothing. |
| 5xx on a GET | Yes | Once or twice. |
| 5xx on a POST | No | Unless idempotency-protected. |

## Checkout: the one flow worth getting exactly right

`POST /checkout/` is the only call where a wrong retry costs a customer a duplicate
order. The protection is the `Idempotency-Key` header, and it only works if you use it
the way the server expects (`apps/core/idempotency.py`):

- **One key per checkout attempt.** Generate it with `crypto.randomUUID()` when the user
  reaches the review-and-pay step, and hold it in state.
- **Same key + same body** → the server replays the stored response. Retrying a timeout
  is safe, and you get the original order back rather than a second one.
- **Same key + different body** → `409 IDEMPOTENCY_KEY_CONFLICT`. If the user edits the
  cart or address, throw the key away and mint a new one.
- **Same key, still running** → also `409 IDEMPOTENCY_KEY_CONFLICT`, with the message
  "Duplicate request is already being processed." Treat this as "wait and retry", not a
  failure. You can't tell the two 409s apart by code, so retry once after ~1s; if it
  conflicts again, refetch `GET /orders/` and check whether the order actually landed.
- Keys are cached for `IDEMPOTENCY_KEY_TTL_SECONDS` (24h by default).

```ts
async function placeOrder(input: CheckoutInput, key: string) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await api.post("/checkout/", input, { headers: { "Idempotency-Key": key } });
      return res.data.data;
    } catch (e) {
      const err = toApiError(e);
      const retryable = err.code === "IDEMPOTENCY_KEY_CONFLICT" || err.code === "NETWORK_ERROR";
      if (!retryable || attempt === 1) throw e;
      await new Promise((r) => setTimeout(r, 1200));
    }
  }
}
```

If both attempts fail, do **not** show "order failed" — show "we're confirming your
order" and check `GET /orders/` before telling the user anything definite.

## Related

- [`ENDPOINTS.md`](./ENDPOINTS.md) — which endpoint can raise what.
- [`PAYMENTS.md`](./PAYMENTS.md) — payment-specific failure handling.
- [`../api/errors.md`](../api/errors.md) — the backend-side view of the same codes.
