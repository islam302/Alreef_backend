# Cash on delivery

**Status: live.** The only payment method this project can complete today, and the
only one verified end-to-end against a real database: register → browse → cart →
checkout → order confirmation → inventory deduction → delivery → cash collected.

`apps/payments/providers/cod.py`. Configuration and the full provider table are in
[PROVIDERS.md](PROVIDERS.md).

## Why COD needs no external API

There is nothing to integrate with. Cash changes hands physically, and the
"gateway" is a person with a bag. Every method of the `PaymentProvider` interface
is implemented with real local logic; none raises `NotImplementedError` except
`parse_webhook_event`, which is correct — COD receives no webhooks, and if
something POSTs to that endpoint it should fail loudly rather than fabricate an
event. (The endpoint does not exist at all: `cod` is not in the webhook
allowlist.)

## The bug this file used to describe

This document previously described checkout calling `mark_payment_captured(payment)`
inline, so that every COD payment was `captured` the moment the customer pressed
confirm. That was wrong, and the wording made it sound deliberate.

**A COD payment is `pending` from checkout until somebody records that the courier
collected.** Before the fix:

* The books said the money was ours before a courier had left the building.
* `captured` is the state refunds are issued from, so a cancelled COD order could
  be "refunded" cash nobody had ever handed over.
* The customer received a "payment received" email while the parcel was still on a
  shelf.

The order is still confirmed at checkout — it is a real order, the honey is spoken
for, the stock is consumed and the confirmation email goes out. Only the *payment*
waits, and only the "payment received" email is withheld.

```python
# apps/checkout/services.py
if payment_method == PaymentMethodSnapshot.COD:
    confirm_order(order, payment_received=False)
```

## Collection

Delivery is when the cash becomes ours.

```python
# apps/orders/services.py — transition_status
if new_status == OrderStatus.DELIVERED:
    _collect_cash_on_delivery(order, collected_by=changed_by)
```

Hooked to the **status transition**, not to one dashboard button, so it holds for
every route that can mark an order delivered: the admin API, the Django admin, and
any courier integration added later.

Failures there are logged, never raised. The parcel *has* been delivered, and
refusing that fact because a bookkeeping step stumbled would leave the order
contradicting the physical world. An uncaptured COD payment is visible in the
dashboard and fixable; a delivery we refused to record is not.

Staff can also record the outcome directly — for cash taken before the status
caught up, or a parcel refused on the doorstep:

```
POST /api/v1/payments/{id}/settle/            {"outcome": "received"|"refused", "note": "..."}
POST /api/v1/admin-api/payments/{id}/settle/  (same, from the dashboard)
```

(`collected` is accepted as a synonym for `received` — it is the natural word for
cash. The same endpoint serves InstaPay, which is settled the same way.)

Both are staff-only. `record_manual_receipt` writes an audit entry naming the
actor, because for COD there is no gateway to corroborate the claim — the person
who said so is the only evidence there is. A customer cannot mark their own order
collected; `tests/test_security_authz.py` asserts it.

## Interface behaviour

| Method | Behaviour |
|---|---|
| `create_payment` | Always succeeds. Local reference `COD-{12 hex}`, status **`pending`**, metadata carries the Arabic instruction and the amount to have ready. No network call. |
| `authorize` | Succeeds. Accepting the order *is* the authorization. |
| `capture` | Succeeds. Reached only from `record_manual_receipt`, never from checkout. |
| `cancel` | `True` — nobody has paid, so there is nothing to undo. |
| `refund` | Succeeds, with a `MANUAL-COD-{10 hex}` reference. Records that a human will hand the cash back or issue store credit; the prefix makes it obvious in reconciliation that no gateway was involved. |
| `get_status` | Echoes our own stored status. There is no external record to compare against, and saying so is honest rather than lazy. |
| `verify_webhook` | Always `False`. |
| `parse_webhook_event` | Raises. |

## Configuration

Every number is a commercial decision, so every number is configurable.

```
COD_ENABLED=True
COD_FEE_AMOUNT=15.00
COD_MIN_ORDER_AMOUNT=0.00
COD_MAX_ORDER_AMOUNT=50000.00          # 0 means no ceiling
COD_ALLOWED_SHIPPING_METHOD_IDS=       # empty means any carrier
```

All five are **enforced** in `calculate_totals`, before an order row exists. The
minimum is checked against the order's own value *before* the COD fee is added — a
minimum that its own surcharge satisfies would be no minimum at all. This document
previously noted that the min/max settings existed but were not enforced; they are
now, and `tests/test_payments_flows.py` covers each one.

`COD_ALLOWED_SHIPPING_METHOD_IDS` exists for the carrier that will not handle cash.
Empty is right until there is one.

## Refunds

Permitted and recorded, but not executed by anything: somebody hands the cash back
or issues store credit. The dashboard shows that wording next to the button rather
than implying a gateway will move the money, and `config.refund_available("cod")`
is where the wording lives.

A refund is still refused unless the payment is `captured` or
`partially_refunded` — which is the whole point of the state fix. A pending COD
order holds no money to give back.

## Reconciliation

COD is excluded from `reconcile_pending_payments_task`, as before, and now for a
stated reason: its `get_status` returns our own record, so polling it cannot learn
anything. COD payments resolve by staff action, not by asking a provider.
