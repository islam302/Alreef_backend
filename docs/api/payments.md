# Payments

Base path: `/api/v1/payments/`

Source: `apps/payments/{urls,views,serializers,models,services}.py`, `apps/payments/providers/*`.

## Current implementation status — read this first

The payment layer is an adapter interface
(`apps/payments/providers/base.py:PaymentProvider`) with one implementation per
**settlement route**, and order/checkout code only ever calls that interface.

| Route | Adapter | Methods it serves | Status |
|---|---|---|---|
| **COD** | `providers/cod.py` | `cod` | **Live.** No external call. The payment stays `pending` until the courier collects — moving the order to `DELIVERED` captures it. |
| **InstaPay** | `providers/instapay.py` | `instapay` | **Live once configured.** No external call, because InstaPay publishes no merchant API: the customer transfers from their banking app and a staff member confirms. |
| **Paymob** | `providers/paymob.py` | `card`, `mobile_wallet`, and `instapay` when Paymob enables it | **Implemented, never run against Paymob.** Intention API, HMAC-SHA512 callback verification, refund, void and transaction inquiry are all written against Paymob's current official documentation and tested against fixtures of those shapes. No Paymob environment has answered it, because that needs merchant onboarding. |

**Fawry, valU, Sympl and Aman were removed.** They were implemented and then taken
out at the business's request, along with their model choices. Asking the registry
for one raises; posting to a webhook URL for one 404s.

**Only `cod` and `instapay` should be described as live**, and `instapay` only once
`INSTAPAY_ADDRESS` is set. Selecting an unavailable method fails with
`PAYMENT_METHOD_NOT_AVAILABLE` (400) from `/checkout/summary/` and `/checkout/`,
raised in `calculate_totals` before an order row exists — so there is nothing to
roll back and nothing partial left behind.

`docs/payments/PROVIDERS.md` is the operator-facing version of this table, with the
merchant setup each route still needs.

## GET /payments/methods/

Public, unpaginated. **The single source of truth for which methods to offer.** A
client must not keep its own list; the storefront used to, and it kept offering a
method after its credentials were pulled.

```json
{"success": true, "data": [
  {"code": "cod", "label_ar": "الدفع عند الاستلام", "label_en": "Cash on delivery",
   "instructions_ar": "تدفع قيمة الطلب كاش للمندوب وقت التسليم.",
   "redirects": false, "sort_order": 10, "cod_fee": "15.00"}
]}
```

A method appears only when its route is enabled **and** every credential its
adapter will reach for is present. The response never says why a method is missing
— that names our configuration. Staff get the reasons from
`GET /admin-api/payment-methods/`.

## Staff endpoints

| Endpoint | What it does |
|---|---|
| `POST /payments/{id}/reconcile/` | Ask the gateway what really happened and apply it. The answer to a lost webhook. |
| `POST /payments/{id}/settle/` | Confirm a manually-settled payment arrived (`{"outcome": "received"}`) or did not (`"refused"`). Covers COD and InstaPay. |
| `POST /payments/{id}/refund/` | Capped at the remaining balance, idempotent, audited. |

All three are `IsAdminUser`. `settle` is the only way an InstaPay order ever becomes
paid, and it writes an audit entry naming the actor — for a manually-settled route
that actor is the only evidence the money arrived.

## Payment / Refund models

`Payment` (`apps/payments/models.py`): one row per checkout attempt for an `Order`.
- `provider` (the settlement route): `paymob | instapay | cod | manual`
- `method` (`PaymentMethodSnapshot`, mirrors `Order.payment_method`): `card | mobile_wallet | instapay | cod`
- `status`: `pending | requires_action | authorized | captured | refund_pending | failed | cancelled | expired | refunded | partially_refunded` — the transitions are in `apps/payments/state.py` and nothing writes this field without going through them; see `docs/payments/STATES.md`
- `merchant_reference`: the reference *we* issued and demand back on a callback
- `failure_reason`: the provider's own words for the last failure
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
- Calls `provider.refund()`. For COD and InstaPay this records a refund a person performs — cash handed back, or a transfer sent back — with a `MANUAL-` prefixed reference so reconciliation can tell it apart. For Paymob it calls the Accept refund endpoint, which needs `PAYMOB_API_KEY` (a different credential from the secret key); without it the call fails with a message saying so.
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

See `docs/api/webhooks.md` for the full `POST /api/v1/payments/webhooks/{provider}/` contract (public, signature-verified, idempotent). Paymob is the only route with a webhook: cash on delivery and InstaPay are settled by a person, so their webhook URLs do not exist and return 404. A webhook arriving while Paymob is disabled at our end is accepted with `202` — so the provider stops retrying — logged as an error, and not processed.
