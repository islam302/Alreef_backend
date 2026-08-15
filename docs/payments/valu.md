# valU (BNPL)

**Status: interface-complete adapter skeleton, not production-ready.**
Every method exists with the correct signature but raises
`NotImplementedError` — no HTTP calls to valU are implemented.

File: `apps/payments/providers/valu.py`.

## What exists today

```python
class ValuPaymentProvider(PaymentProvider):
    code = "valu"

    def __init__(self):
        self.config = settings.PAYMENT_PROVIDERS["VALU"]
        if not self.config["ENABLED"]:
            raise NotImplementedError(
                "valU is not enabled. Set VALU_ENABLED=True plus API_KEY once you have verified valU's "
                "official API documentation and merchant credentials."
            )

    # create_payment, authorize, capture, cancel, refund, get_status,
    # verify_webhook, parse_webhook_event — all raise NotImplementedError.
```

Module docstring:

> "valU BNPL adapter skeleton. Requires a merchant agreement and API
> credentials from valU directly — no public sandbox is documented here.
> Interface shape only; do not enable without verified official docs.
> Sympl and Aman (also referenced in spec §40) follow the same pattern and
> should be added as sibling modules once their official APIs are verified."

valU is a buy-now-pay-later (BNPL/installments) provider — its integration
model (credit approval flow, installment scheduling) is architecturally
different from a simple card capture, and no public sandbox documentation is
referenced, so this skeleton is even more clearly a placeholder than
Paymob/Fawry: it exists purely to reserve the shape (`code = "valu"`, the
config block, the `PAYMENT_PROVIDERS["VALU"]` settings entry) so checkout
logic already knows how to route to it once a real merchant agreement and
API docs exist.

Sympl and Aman (also referenced in the codebase's `PaymentMethodSnapshot`
choices in `apps/orders/models.py` — `sympl`, `aman`) are also mapped in
`apps.checkout.services.PAYMENT_METHOD_PROVIDER_MAP` (to provider codes
`"sympl"` and `"aman"` respectively) but **no adapter module exists for
either** — attempting to use them will fail at
`apps.payments.providers.get_payment_provider()` since there is no
registered provider for those codes yet. They are placeholders one level
earlier than valU.

## Environment variables required (`.env.example` / `config/settings/base.py`)

```
VALU_ENABLED=False
VALU_API_KEY=
VALU_BASE_URL=
```

These map directly to `settings.PAYMENT_PROVIDERS["VALU"]`. Note
`VALU_BASE_URL` has no default value at all in `.env.example` — unlike
Paymob/Fawry, there isn't even a public staging URL to fall back to.

## What real integration would require

1. A direct merchant agreement with valU — this is not a self-service
   sandbox signup like Paymob/Fawry.
2. valU's official, current API documentation for the BNPL flow: customer
   credit-check/approval, installment plan selection, payment creation, and
   webhook/callback shape for approval and payment status.
3. Implementing every `PaymentProvider` method against that verified spec —
   `create_payment` in particular likely needs to represent a
   pending-approval state distinct from a simple card auth, which may
   require extending `PaymentStatus` choices (`apps/payments/models.py`) if
   valU's flow doesn't map cleanly onto `pending`/`authorized`/`captured`.
4. Implementing `Sympl` and `Aman` as sibling adapter modules following the
   same pattern once their respective official APIs are similarly verified —
   these do not exist yet even as skeletons.

Once implemented, `apps.checkout.services.PAYMENT_METHOD_PROVIDER_MAP`
already maps the `valu` payment method to provider code `"valu"`, and the
shared webhook endpoint (`POST /api/v1/payments/webhooks/valu/`) and
reconciliation task already route to it.
