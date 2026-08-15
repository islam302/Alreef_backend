# Payments

Files: `apps/payments/models.py`, `apps/payments/providers/` (`base.py`,
`cod.py`, `paymob.py`, `fawry.py`, `valu.py`), `apps/payments/services.py`,
`apps/payments/views.py`, `apps/payments/tasks.py`.

## Provider abstraction

Every gateway implements the same abstract interface,
`apps.payments.providers.base.PaymentProvider` — order/checkout code never
imports a vendor SDK directly, only this interface (docstring: "spec §41...
Order/checkout code only ever talks to `PaymentProvider`, never a specific
vendor SDK"):

```python
class PaymentProvider(ABC):
    code: str = "base"

    def create_payment(self, *, payment, order) -> CreatePaymentResult: ...
    def authorize(self, *, payment) -> CaptureResult: ...
    def capture(self, *, payment, amount=None) -> CaptureResult: ...
    def cancel(self, *, payment) -> bool: ...
    def refund(self, *, payment, amount, reason: str = "") -> RefundResult: ...
    def get_status(self, *, payment) -> StatusResult: ...
    def verify_webhook(self, *, headers: dict, body: bytes, query_params: dict) -> bool: ...
    def parse_webhook_event(self, *, headers: dict, body: bytes, query_params: dict) -> dict: ...
```

`apps.payments.providers.get_payment_provider(code)` is the factory that
resolves a provider code (`cod`/`paymob`/`fawry`/`valu`) to a concrete
instance. Result types (`CreatePaymentResult`, `CaptureResult`,
`RefundResult`, `StatusResult`) are plain dataclasses so every provider,
implemented or not, returns a uniform shape.

## Model relationships

- **`Payment`** — one row per checkout attempt against an `Order`
  (`order` FK, `on_delete=PROTECT`). Holds `provider`, `method` (the
  customer-facing method: card/wallet/fawry_pay/valu/sympl/aman/cod),
  `amount` (`MoneyField`), `status`, `provider_reference` (indexed),
  `idempotency_key` (indexed), `expires_at`, and a `metadata` JSON field
  explicitly documented as **non-sensitive only** — "never card numbers,
  CVV, or secrets" (`apps/payments/models.py`).
- **`PaymentAttempt`** — per-attempt record (`attempt_number`,
  `unique_together (payment, attempt_number)`) for gateways that allow retry
  within one `Payment`.
- **`PaymentTransaction`** — one row per provider round-trip
  (`transaction_type`: authorize/capture/refund/void), storing
  `provider_transaction_id`, `success`, and a **redacted** `raw_response`
  (`apps.core.logging_filters.redact`, applied by `_redact()` in
  `apps/payments/services.py` before every write) — this is the
  reconciliation/audit trail for every gateway interaction.
- **`PaymentWebhookEvent`** — one row per inbound webhook delivery.
  `provider_event_id` is **globally unique** — this is the entire replay
  defense (see below).
- **`Refund`** — `payment` + `order` FKs (both `PROTECT`), `amount`,
  `destination` (`original_method` / `store_credit`), `status`
  (`pending`/`processed`/`failed`), `idempotency_key`.

```
Order 1──* Payment 1──* PaymentAttempt
                1──* PaymentTransaction
                1──* PaymentWebhookEvent
                1──* Refund
```

## Idempotency strategy

Two independent, complementary mechanisms:

1. **Client-initiated requests** (payment creation happens implicitly inside
   checkout; refund creation is explicit): `RefundCreateView` mixes in
   `apps.core.idempotency.IdempotentRequestMixin` exactly like checkout (see
   `docs/architecture/checkout.md`) — an `Idempotency-Key` header plus a
   Redis lock keyed by `(scope, key)` guarantees a retried "create refund"
   POST is executed at most once. `create_refund()` in
   `apps/payments/services.py` *also* independently checks
   `Refund.objects.filter(idempotency_key=...)` as a second, DB-level guard.
2. **Provider-initiated webhooks**: `PaymentWebhookEvent.provider_event_id`
   is a `unique=True` column. `process_webhook()` does
   `get_or_create(provider_event_id=parsed["event_id"], defaults={...})` — if
   the row already exists (`created=False`), the function returns
   immediately without touching the `Payment` again. This makes a
   gateway retrying the same webhook delivery (which all major Egyptian
   gateways do to guarantee at-least-once delivery) a guaranteed no-op,
   regardless of how many times it arrives or in what order relative to
   other events (spec reference in code: "spec §42/§89 replay protection").

Both `mark_payment_captured` and `mark_payment_failed` additionally take a
row lock (`Payment.objects.select_for_update()`) inside `@transaction.atomic`,
so a webhook retry racing a manual admin action (or two near-simultaneous
webhook deliveries before the first commits) cannot double-capture or
double-fail the same payment.

## Webhook endpoint

`POST /api/v1/payments/webhooks/<provider>/` (`PaymentWebhookView`,
`AllowAny`, no authentication — gateways call this unauthenticated and prove
themselves via signature) throttled at `payment_webhook: 120/min`. Flow:

1. Reject unknown `provider` values (`ErrorCodes.NOT_FOUND`).
2. `apps.payments.services.process_webhook()`:
   - `provider.verify_webhook(headers, body, query_params)` — must return
     `True` or the request is rejected with
     `PAYMENT_WEBHOOK_INVALID_SIGNATURE` (400). **COD's `verify_webhook`
     always returns `False`** — COD has no webhooks by design.
   - `provider.parse_webhook_event(...)` extracts a normalized
     `{event_id, event_type, provider_reference, status, payload}`.
   - Dedupe via `PaymentWebhookEvent.provider_event_id` as above.
   - Looks up the `Payment` by `provider_reference` and calls
     `mark_payment_captured`/`mark_payment_failed` based on the normalized
     `status`.
3. If the provider raises `NotImplementedError` (i.e., a webhook arrives for
   a provider that isn't actually wired up yet), the view logs a warning and
   returns HTTP 202 rather than 500 — acknowledging receipt without
   pretending to have processed it.

## Payment status polling

`GET /api/v1/payments/<pk>/status/` (`PaymentStatusView`) — the frontend
polls this after redirecting back from a gateway. Its docstring is explicit
about the security model: "The frontend must never mark a payment successful
itself — only this server-verified status is authoritative." It calls
`provider.get_status(payment=payment)` and updates the stored status if
different; `NotImplementedError` from an unconfigured provider is swallowed
(falls back to relying on webhooks / reconciliation).

## Reconciliation

`apps.payments.tasks.reconcile_pending_payments_task` (Celery Beat, every 15
minutes — `config/settings/base.py` `CELERY_BEAT_SCHEDULE`) polls
`get_status()` for any `Payment` stuck in `PENDING`/`AUTHORIZED` — **explicitly
excluding COD** (`exclude(provider="cod")`), since COD resolves on physical
delivery, not by gateway polling. `NotImplementedError` from an unconfigured
provider is caught and skipped per payment, so this task is safe to run even
when only COD is enabled.

## Provider status — be honest

| Provider | `create_payment` | `verify_webhook` | Status |
|---|---|---|---|
| **COD** (`cod.py`) | Fully implemented, no external API | Always returns `False` (no webhooks) | **Production-ready.** Verified end-to-end live: register → browse → add to cart → checkout → order confirmation → inventory deduction. |
| **Paymob** (`paymob.py`) | Raises `NotImplementedError` unless `PAYMOB_ENABLED=True`, then raises anyway | Raises `NotImplementedError` | **Interface-complete skeleton only.** Every method exists with the correct signature and docstrings describing the intended flow (auth → order registration → payment key → iframe/redirect, HMAC webhook verification), but no HTTP calls are implemented. |
| **Fawry** (`fawry.py`) | Same pattern | Same pattern | **Interface-complete skeleton only.** |
| **valU** (`valu.py`) | Same pattern | Same pattern | **Interface-complete skeleton only.** No public sandbox is documented; requires a direct merchant agreement. |

This is a deliberate project policy, stated directly in the source:

> "...this module defines the correct shape of the integration ... but
> intentionally does NOT fabricate request/response field names. Wire the
> real HTTP calls against Paymob's official, current API reference once real
> sandbox credentials are available; until then all methods raise
> `NotImplementedError` so nothing pretends to work." — `apps/payments/providers/paymob.py`

Each gateway's `__init__` first checks its `ENABLED` flag
(`settings.PAYMENT_PROVIDERS["PAYMOB"]["ENABLED"]` etc., default `False`,
`.env.example`) and raises `NotImplementedError` immediately if disabled —
so even instantiating the provider fails loudly rather than silently
returning fake success. See `docs/payments/paymob.md`, `docs/payments/fawry.md`,
`docs/payments/valu.md`, `docs/payments/cod.md` for the per-provider detail
and required environment variables.

## Refunds

`POST /api/v1/payments/<pk>/refund/` (`RefundCreateView`, `IsAdminUser`)
calls `apps.payments.services.create_refund()`:

1. Locks the `Payment` row.
2. Idempotency-key short-circuit (see above).
3. Sums all previously `PROCESSED` refunds and rejects if
   `already_refunded + amount > payment.amount`
   (`ErrorCodes.REFUND_EXCEEDS_CAPTURED_AMOUNT`).
4. Calls `provider.refund(...)`. On success, updates `Payment.status` to
   `PARTIALLY_REFUNDED` or `REFUNDED` depending on whether the payment is
   now fully refunded; on failure, `Refund.status = FAILED`.
5. Records a `PaymentTransaction(REFUND)` (redacted raw response), queues a
   `refund` confirmation email on success, and writes an `AuditLog` row via
   `apps.audit.services.record(action=AuditAction.REFUND, ...)`.

`apps.orders.services.complete_return_and_refund` is the caller for the
returns flow — it computes the refund amount from approved `ReturnItem`
quantities × their `OrderItem.unit_price` and invokes `create_refund` against
the order's most recent payment.
