# Paymob

**Status: interface-complete adapter skeleton, not production-ready.**
Every method exists with the correct signature but raises
`NotImplementedError` — no HTTP calls to Paymob are implemented. This is a
deliberate project policy, not an oversight: the code must never fabricate
undocumented request/response formats for a real payment gateway.

File: `apps/payments/providers/paymob.py`.

## What exists today

```python
class PaymobPaymentProvider(PaymentProvider):
    code = "paymob"

    def __init__(self):
        self.config = settings.PAYMENT_PROVIDERS["PAYMOB"]
        if not self.config["ENABLED"]:
            raise NotImplementedError(
                "Paymob is not enabled. Set PAYMOB_ENABLED=True plus API_KEY/INTEGRATION_ID/HMAC_SECRET "
                "once you have verified Paymob's current official API documentation and sandbox credentials."
            )

    def create_payment(self, *, payment, order) -> CreatePaymentResult:
        raise NotImplementedError(...)
    # authorize, capture, cancel, refund, get_status, verify_webhook,
    # parse_webhook_event — all raise NotImplementedError.
```

Even instantiating the provider fails immediately unless
`PAYMOB_ENABLED=True` is set — so a misconfigured deployment fails loudly at
the point of use (`apps.checkout.services.create_order_from_cart` catches
this and surfaces it as `ApplicationError(PAYMENT_PROVIDER_UNAVAILABLE)` to
the customer) rather than silently pretending to accept card payments.

The module docstring states the intended shape explicitly:

> "Paymob (Accept) adapter skeleton. Paymob requires a live merchant
> account, API key, and integration IDs (card / wallet) obtained from
> https://accept.paymob.com — this module defines the correct shape of the
> integration (auth → order registration → payment key → iframe/redirect,
> HMAC webhook verification) but intentionally does NOT fabricate
> request/response field names."

And on webhook verification specifically:

> "Paymob signs callbacks with an HMAC over a documented, ordered set of
> transaction fields (see Paymob's official 'HMAC Calculation' guide).
> Implement the exact field order from that guide before enabling — an
> incorrect order silently accepts forged webhooks."

That last point is the key reason this cannot be safely guessed at: an HMAC
verification implemented against a wrong field order doesn't fail loudly —
it silently accepts forged payment-success webhooks, which is a critical
security bug. It must be implemented against Paymob's current, official
documentation.

## Environment variables required (`.env.example` / `config/settings/base.py`)

```
PAYMOB_ENABLED=False
PAYMOB_API_KEY=
PAYMOB_INTEGRATION_ID_CARD=
PAYMOB_INTEGRATION_ID_WALLET=
PAYMOB_IFRAME_ID=
PAYMOB_HMAC_SECRET=
PAYMOB_BASE_URL=https://accept.paymob.com/api
```

These map directly to `settings.PAYMENT_PROVIDERS["PAYMOB"]`.

## What real integration would require

1. A verified Paymob merchant account (card + mobile wallet integration IDs
   are separate).
2. Reading Paymob's current, official API reference at the time of
   implementation (their auth → order registration → payment key →
   iframe/redirect flow, and their exact HMAC field-order specification for
   webhook verification) — not this document, not training data, since
   payment gateway APIs change and guessing at request/response field names
   for a live payment integration is exactly the kind of fabrication this
   codebase's policy exists to prevent.
3. Implementing `create_payment` (auth token → order registration → payment
   key request → return an iframe/redirect URL), `authorize`/`capture` (if
   Paymob's flow separates these), `refund`, `get_status`, `verify_webhook`
   (HMAC per the official field order), and `parse_webhook_event` against
   that verified spec.
4. Testing against Paymob's sandbox before setting `PAYMOB_ENABLED=True` in
   any environment that takes real payments.

Once implemented, this provider plugs into the rest of the system with zero
changes elsewhere — `apps.payments.providers.get_payment_provider("paymob")`,
`apps.checkout.services.PAYMENT_METHOD_PROVIDER_MAP` (which already maps
`card`/`mobile_wallet` → `paymob`), the webhook endpoint
(`POST /api/v1/payments/webhooks/paymob/`), and the reconciliation task all
already expect and route to this provider code — only the adapter's internal
HTTP logic is missing.
