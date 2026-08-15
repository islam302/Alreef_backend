# Fawry (FawryPay)

**Status: interface-complete adapter skeleton, not production-ready.**
Every method exists with the correct signature but raises
`NotImplementedError` — no HTTP calls to FawryPay are implemented.

File: `apps/payments/providers/fawry.py`.

## What exists today

```python
class FawryPaymentProvider(PaymentProvider):
    code = "fawry"

    def __init__(self):
        self.config = settings.PAYMENT_PROVIDERS["FAWRY"]
        if not self.config["ENABLED"]:
            raise NotImplementedError(
                "Fawry is not enabled. Set FAWRY_ENABLED=True plus MERCHANT_CODE/SECURITY_KEY once you have "
                "verified Fawry's current official API documentation and sandbox credentials."
            )

    # create_payment, authorize, capture, cancel, refund, get_status,
    # verify_webhook, parse_webhook_event — all raise NotImplementedError.
```

Module docstring:

> "FawryPay adapter skeleton. Requires merchant onboarding
> (FAWRY_MERCHANT_CODE, FAWRY_SECURITY_KEY) via
> https://developer.fawrystaging.com. Implements the provider interface
> shape only — real request signing / charge-request payloads must be wired
> against Fawry's current official API reference once sandbox credentials
> are available."

Note "request signing" specifically — like Paymob's HMAC verification,
Fawry's charge-request signing scheme must be implemented exactly per their
current spec; an incorrect implementation is either rejected outright by
Fawry (safe failure) or, worse for webhook verification, could silently
accept unsigned/forged requests. Neither `verify_webhook` nor
`parse_webhook_event` are implemented, for the same reason.

## Environment variables required (`.env.example` / `config/settings/base.py`)

```
FAWRY_ENABLED=False
FAWRY_MERCHANT_CODE=
FAWRY_SECURITY_KEY=
FAWRY_BASE_URL=https://atfawry.fawrystaging.com
```

These map directly to `settings.PAYMENT_PROVIDERS["FAWRY"]`. Note the
default `FAWRY_BASE_URL` already points at Fawry's *staging* environment —
production would need the live base URL from Fawry's onboarding.

## What real integration would require

1. Merchant onboarding with Fawry (`FAWRY_MERCHANT_CODE` +
   `FAWRY_SECURITY_KEY`) via `https://developer.fawrystaging.com`.
2. Reading Fawry's current, official API reference — their charge-request
   payload shape, signature algorithm, and reference/payment-code flow
   (Fawry's model typically involves generating a payment reference number
   the customer pays at a physical Fawry outlet or via their app, which is
   architecturally different from Paymob's iframe/redirect flow — the exact
   shape must come from Fawry's docs, not be assumed here).
3. Implementing `create_payment`, `capture`/`authorize` as applicable to
   Fawry's flow, `refund`, `get_status`, `verify_webhook`, and
   `parse_webhook_event` against that verified spec.
4. Testing end-to-end against Fawry's staging environment before enabling
   `FAWRY_ENABLED=True` against production credentials.

Once implemented, no other part of the system needs to change —
`apps.checkout.services.PAYMENT_METHOD_PROVIDER_MAP` already maps
`fawry_pay` → `fawry`, and the shared webhook endpoint
(`POST /api/v1/payments/webhooks/fawry/`) and reconciliation task already
route to this provider code.
