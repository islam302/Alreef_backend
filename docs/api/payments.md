# Payments

Base path: `/api/v1/payments/`

Source: `apps/payments/{urls,views,serializers,models,services}.py`, `apps/payments/providers/*`.

## Current implementation status — read this first

The payment-provider layer is built as a clean adapter interface (`apps/payments/providers/base.py:PaymentProvider`) with one concrete implementation per gateway, and order/checkout code only ever calls this interface — never a vendor SDK directly. However:

| Provider | Adapter file | Status |
|---|---|---|
| **COD** (cash on delivery) | `apps/payments/providers/cod.py` | **Fully live.** No external call — captures immediately at order creation. |
| **Paymob** (cards, mobile wallets) | `apps/payments/providers/paymob.py` | Architecturally complete adapter (auth → order registration → payment key → iframe/redirect, HMAC webhook verification) but **every method raises `NotImplementedError`**. Enabling it means wiring real HTTP calls against Paymob's current official API reference with real sandbox/merchant credentials — the code deliberately does not fabricate field names to "look done." |
| **FawryPay** | `apps/payments/providers/fawry.py` | Same status: adapter skeleton, `NotImplementedError` until verified against Fawry's official docs + merchant onboarding. |
| **valU** (BNPL) | `apps/payments/providers/valu.py` | Same status: adapter skeleton, `NotImplementedError` until verified against valU's official docs + merchant credentials. |
| **Sympl, Aman** | — | `payment_method` choices exist on `Order`/`PaymentMethodSnapshot` (`sympl`, `aman`) but there is **no provider adapter file at all** yet; `apps.checkout.services.PAYMENT_METHOD_PROVIDER_MAP` doesn't map them to a provider, so selecting them at checkout fails with `PAYMENT_PROVIDER_UNAVAILABLE`. |

**Do not describe Paymob/Fawry/valU as "working" in any downstream documentation or frontend integration guide.** Only `cod` completes an order end-to-end today. When a checkout selects an unimplemented/unmapped `payment_method`, `create_order_from_cart` catches the `NotImplementedError` and surfaces it as `ApplicationError(PAYMENT_PROVIDER_UNAVAILABLE)` — the whole order transaction is rolled back, so no partial order is left behind.

## Payment / Refund models

`Payment` (`apps/payments/models.py`): one row per checkout attempt for an `Order`.
- `provider`: `paymob | fawry | valu | cod | manual`
- `method` (`PaymentMethodSnapshot`, mirrors `Order.payment_method`): `card | fawry_pay | mobile_wallet | valu | sympl | aman | cod`
- `status`: `pending | authorized | captured | failed | cancelled | expired | refunded | partially_refunded`
- `metadata`: JSON — **non-sensitive only** (redirect/iframe URLs, provider reference); card numbers/CVV/secrets are never stored
- Every provider round-trip is also recorded in `PaymentTransaction` (`authorize | capture | refund | void`) with `raw_response` passed through `apps.core.logging_filters.redact()` before storage

`Refund`: `amount, reason, destination (original_method|store_credit), status (pending|processed|failed), provider_refund_id, idempotency_key`.

## GET /api/v1/payments/{id}/

Auth: `IsAuthenticated`, scoped to `order__user=request.user`.

`PaymentSerializer` (read-only):
```json
{
  "success": true, "message": "Success",
  "data": {
    "id": 210, "order_number": "ORD-20260814-00042", "provider": "cod", "method": "cod",
    "amount": "870.00", "status": "captured", "provider_reference": "", "metadata": {}, "created_at": "2026-08-14T10:20:00Z"
  },
  "errors": null
}
```

Errors: `NOT_FOUND` (404).

## GET /api/v1/payments/{id}/status/

Auth: `IsAuthenticated`, same ownership scoping. Meant to be **polled by the frontend after redirect back from a hosted gateway page** — the frontend must never mark a payment successful client-side; this endpoint (backed by `provider.get_status()`) is the only authoritative source. If the live provider status differs from the stored one, the row is updated before responding.

For providers without a working `get_status()` implementation (all except COD today), the `NotImplementedError` is swallowed and the endpoint simply returns the **last known stored status** — polling silently falls back to whatever a webhook last recorded rather than erroring.

Response shape: identical to `GET /payments/{id}/`.

## POST /api/v1/payments/{id}/refund/

Auth: **`IsAdminUser`** (staff only — not exposed to customers directly; customer-initiated refunds flow through the returns process, see `docs/api/orders.md`). Requires `Idempotency-Key` header (same contract as checkout — a retried refund request with the same key+body returns the original result instead of double-refunding).

Request (`RefundCreateSerializer`):
```json
{ "amount": "450.00", "reason": "Damaged item returned", "destination": "original_method" }
```
`destination` is `original_method` (default) or `store_credit`.

Business logic (`apps/payments/services.py:create_refund`):
- Sums all previously `processed` refunds on the payment; rejects if `already_refunded + amount > payment.amount` with `REFUND_EXCEEDS_CAPTURED_AMOUNT` (409).
- Calls `provider.refund()` — for COD this simply succeeds (there is no external capture to reverse; it's a bookkeeping-only refund path such as store credit); for Paymob/Fawry/valU this raises `NotImplementedError` today.
- On success, sets `Payment.status` to `refunded` (full) or `partially_refunded`, sends a "Refund processed" email, and writes an `AuditAction.REFUND` audit log entry.

Success `201` (`RefundSerializer`):
```json
{
  "success": true, "message": "Refund processed.",
  "data": {
    "id": 15, "payment": 210, "order": 331, "amount": "450.00", "reason": "Damaged item returned",
    "destination": "original_method", "status": "processed", "provider_refund_id": "", "processed_at": "2026-08-14T12:00:00Z", "created_at": "2026-08-14T11:59:58Z"
  },
  "errors": null
}
```

Errors: `NOT_FOUND` (404, payment missing), `REFUND_EXCEEDS_CAPTURED_AMOUNT` (409), `IDEMPOTENCY_KEY_REQUIRED` (400), `IDEMPOTENCY_KEY_CONFLICT` (409), `PAYMENT_PROVIDER_UNAVAILABLE` (503, gateway adapter not implemented — refund row is created with `status=failed`, not silently dropped).

## Webhooks

See `docs/api/webhooks.md` for the full `POST /api/v1/payments/webhooks/{provider}/` contract (public endpoint, signature-verified, idempotent). Note that because Paymob/Fawry/valU's `verify_webhook()`/`parse_webhook_event()` also raise `NotImplementedError` today, inbound webhooks for those providers are currently accepted with HTTP `202` and logged as "unconfigured provider" rather than processed — this is intentional (never silently trust an unverifiable signature) and will resolve itself once real credentials + verified official docs are wired into each adapter.
