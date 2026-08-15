# Cash on Delivery (COD)

**Status: fully implemented and production-ready.** This is the only
payment method verified end-to-end live against real PostgreSQL: register →
login → browse products → add to cart → checkout with COD → order
confirmation → inventory deduction all tested successfully.

File: `apps/payments/providers/cod.py`.

## Why COD needs no external API

COD is a first-class payment method precisely because it has no gateway to
integrate with — cash changes hands physically when the courier delivers the
order. The code's docstring states this directly: "Cash on delivery — a
first-class payment method (spec §43). No external API calls: the 'capture'
happens physically when the courier collects cash, triggered by staff
marking the order delivered."

## Implementation

`CODPaymentProvider` (`code = "cod"`) implements every method of the
`PaymentProvider` interface (`apps/payments/providers/base.py`) with real,
working logic — none of it raises `NotImplementedError`:

| Method | Behavior |
|---|---|
| `create_payment(payment, order)` | Always succeeds. Generates a local reference `COD-{12 hex chars}` (`uuid.uuid4().hex[:12].upper()`), status `pending`, and stores an `instructions` string in metadata ("Pay in cash to the courier upon delivery."). No network call. |
| `authorize(payment)` | Always succeeds, echoing back `payment.provider_reference`. |
| `capture(payment, amount=None)` | Always succeeds. Docstring: "Called when the order is marked DELIVERED — cash was collected." |
| `cancel(payment)` | Always returns `True`. |
| `refund(payment, amount, reason)` | Always succeeds, generating a `COD-REFUND-{10 hex chars}` reference. Comment: "COD refunds are manual (cash handback / store credit) — recorded for audit but never auto-processed against a gateway." |
| `get_status(payment)` | Returns the payment's own stored `status` — there's no external status to poll. |
| `verify_webhook(...)` | Always returns `False` — "COD has no webhooks." |
| `parse_webhook_event(...)` | Raises `NotImplementedError("COD does not receive webhooks.")` — this is intentional and correct: the webhook endpoint should never be called for the `cod` provider, and if it somehow is, this fails loudly rather than fabricating an event. |

## Flow inside checkout

Unlike gateway methods, `apps.checkout.services.create_order_from_cart`
special-cases COD to confirm the order **immediately**, inline in the same
request/transaction, rather than waiting for an async webhook:

```python
if payment_method == PaymentMethodSnapshot.COD:
    from apps.payments.services import mark_payment_captured
    mark_payment_captured(payment)
    payment.refresh_from_db()
    order.refresh_from_db()
```

`mark_payment_captured()` sets `Payment.status = CAPTURED`, records a
`PaymentTransaction(CAPTURE)`, and calls
`apps.checkout.services.confirm_order_after_payment(order)` — which
transitions the order to `CONFIRMED`, permanently consumes the stock
reservations created during checkout (FEFO batch deduction, see
`docs/architecture/inventory.md`), records coupon usage, queues confirmation
emails, and awards loyalty points. All of this happens before the
`POST /checkout/` response is returned to the customer.

## Configuration

`PAYMENT_PROVIDERS["COD"]` in `config/settings/base.py`, sourced from
environment variables (`.env.example`):

```
COD_ENABLED=True
COD_FEE_AMOUNT=15.00
COD_MIN_ORDER_AMOUNT=0.00
COD_MAX_ORDER_AMOUNT=50000.00
```

`COD_ENABLED` is checked in `apps.checkout.services.calculate_totals` — if
`False`, selecting COD at checkout raises
`ErrorCodes.PAYMENT_PROVIDER_UNAVAILABLE` before an order is ever created.
`COD_FEE_AMOUNT` (default 15.00 EGP) is added to the order's `cod_fee` field
and rolled into `grand_total`. `COD_MIN_ORDER_AMOUNT`/`COD_MAX_ORDER_AMOUNT`
are declared in settings but are not currently enforced inside
`calculate_totals` in the code reviewed — they exist as configuration for a
future validation rule (e.g. rejecting very large COD orders as fraud risk).

## Reconciliation exclusion

`apps.payments.tasks.reconcile_pending_payments_task` (Celery Beat, every 15
minutes) explicitly excludes COD payments:
`Payment.objects.filter(status__in=[PENDING, AUTHORIZED]).exclude(provider="cod")`
— because COD payments resolve at delivery time (staff action), not via
gateway status polling, including them in the reconciliation sweep would be
meaningless (there is no external status to reconcile against).
