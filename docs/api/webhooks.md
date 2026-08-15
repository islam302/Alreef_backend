# Payment Webhooks

Endpoint: `POST /api/v1/payments/webhooks/{provider}/`

Source: `apps/payments/views.py:PaymentWebhookView`, `apps/payments/services.py:process_webhook`, `apps/payments/models.py:PaymentWebhookEvent`.

`{provider}` is one of `PaymentProviderCode` values: `paymob | fawry | valu | cod | manual`. An unknown provider segment returns `404 NOT_FOUND`.

## Auth: none, by design

```python
permission_classes = [permissions.AllowAny]
authentication_classes = []
```

Payment gateways call this URL directly from their own servers — there is no user session, cookie, or JWT to present, and no `Authorization` header is expected or checked. Trust is established entirely by **cryptographic signature verification** (below), not by DRF authentication/permission classes. The endpoint is throttled at `payment_webhook` scope (**120/min**) to absorb bursts of provider retries without becoming a DoS vector.

## Request handling flow

1. `provider.verify_webhook(headers, body, query_params)` — provider-specific signature check (e.g. Paymob's documented HMAC-over-ordered-fields scheme). If this returns `False`, the request is rejected with `PAYMENT_WEBHOOK_INVALID_SIGNATURE` (400) — the payload is **never processed** on a bad signature, regardless of what it claims.
2. `provider.parse_webhook_event(...)` extracts a normalized shape: `{"event_id", "event_type", "provider_reference", "status", "payload"}`.
3. **Idempotent replay protection**: `PaymentWebhookEvent.provider_event_id` has a **unique** database constraint. `process_webhook()` does `get_or_create(provider_event_id=parsed["event_id"], ...)` — if the row already existed (`created=False`), the function returns immediately without touching the `Payment` again. A gateway that retries the same webhook delivery (common — most providers retry on anything but a clean `200`) is a guaranteed no-op the second time, so a payment is never double-captured or double-refunded from webhook replay.
4. If new, the matching `Payment` is looked up by `provider_reference`. `status="captured"` calls `mark_payment_captured()` (which also transitions the order and consumes the stock reservation via `confirm_order_after_payment`); `status="failed"` calls `mark_payment_failed()` (sends a "payment failed" email).
5. The `PaymentWebhookEvent` row is marked `processed=True` with a `processed_at` timestamp, and its `payload` is stored only after passing through `apps.core.logging_filters.redact()` — sensitive fields (card data, secrets) are stripped before anything touches the database.

## Response codes

- `200` — signature valid and event processed (or replayed as a no-op).
- `202` — provider recognized but its adapter isn't implemented yet (Paymob/Fawry/valU today — see `docs/api/payments.md`). The webhook is acknowledged (so the gateway doesn't treat it as a delivery failure and hammer retries) but **not processed**; this is logged as a warning server-side.
- `400` — signature verification failed (`PAYMENT_WEBHOOK_INVALID_SIGNATURE`).
- `404` — `{provider}` isn't a recognized `PaymentProviderCode`.

There is no JSON success envelope on `200`/`202` — the view returns a bare `HttpResponse` with no body, matching what payment gateways typically expect from a webhook receiver.

## Secrets

Webhook signing secrets (`PAYMOB_HMAC_SECRET`, `FAWRY_SECURITY_KEY`, `VALU_API_KEY`, etc.) are read exclusively from environment variables via `django-decouple` (`config/settings/base.py:PAYMENT_PROVIDERS`), sourced from `.env` (see `.env.example` for the full variable list — every value in that file is blank/disabled by default). **They must never be committed to the repository, hard-coded, or written into documentation** — this file intentionally does not reproduce any secret value or example signature.
