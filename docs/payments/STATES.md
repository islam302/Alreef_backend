# Payment states

Defined once, in `apps/payments/state.py`. Nothing writes `Payment.status`
without going through `assert_transition`.

## The states

| Stored value | Meaning | Terminal |
|---|---|---|
| `pending` | Created. Nothing to do but wait — a cash-on-delivery order, or a gateway call not yet answered. | no |
| `requires_action` | The customer has something to do: a 3-D Secure page, a Paymob checkout, an InstaPay transfer to make. They have not paid. | no |
| `authorized` | Money promised, not taken. An authorization without a capture. | no |
| `captured` | **The money is ours.** This is what the brief calls *paid*. | almost |
| `refund_pending` | A refund has been sent to the gateway and not yet confirmed. | no |
| `partially_refunded` | Some of it has been given back. | no |
| `refunded` | All of it has. | yes |
| `failed` | It will not complete. | yes |
| `cancelled` | Voided before settlement, or refused at the door. | yes |
| `expired` | A checkout intention or transfer window timed out unpaid. | yes |

### Why `captured` and not `paid`

The brief lists the state as *paid*. The stored value is `captured`, which is the
accurate term for a captured authorization and — more to the point — is the value
already in the database, the public API, the dashboard's Arabic labels and the
test suite. Renaming it would break a published contract to gain a synonym.

`captured` **is** the paid state. Anything asking "has this been paid?" should test
`status in state.SETTLED`, which is `{captured, partially_refunded}` — a partially
refunded payment is still one where money changed hands.

## Legal transitions

```
pending ──────────► requires_action ──┐
   │                      │           │
   ├──► authorized ◄──────┘           │
   │        │                         │
   ├────────┴──► captured ────────────┤
   │                 │                │
   │                 ├──► refund_pending ──► partially_refunded ──► refunded
   │                 │         │                    │
   │                 │         └──► captured        └──► refunded
   │                 │              (gateway refused)
   │                 └──────────────► refunded
   │
   └──► failed / cancelled / expired            (all terminal)
```

Rules worth stating in words:

* **Nothing leaves a terminal state.** A replayed `failed` event arriving after the
  `captured` that superseded it is refused, so it cannot lose the fact that we hold
  the customer's money. `mark_payment_failed` used to overwrite the status
  unconditionally; that was the bug.
* **Captured money only moves towards being given back.** There is no path from
  `captured` to `failed`.
* **Re-entering the same state is always legal and always a no-op.** This is what
  makes a duplicated webhook harmless rather than an error: the second delivery
  asks for a transition the payment has already made.
* **`requires_action` may repeat.** A checkout session or a transfer reference can
  be re-issued while still unpaid.

## Provider vocabularies

Two different languages, and they must not be mixed.

**`PROVIDER_STATUS_MAP`** translates what a *gateway* says in a webhook or a status
inquiry into one of ours. It is explicit per provider, and a token that is not in
the table raises rather than being guessed at — the caller records the event
unprocessed instead of acting on a guess. `None` means "this event carries no
status change" — an order-created notification, say — which is different from
"unrecognised".

Today there is one gateway with a vocabulary of its own, Paymob. Cash on delivery
and InstaPay have none: their `get_status` echoes our own record back, because our
record is the only record, so their mapping is the identity over our own values.

Fawry's vocabulary (`PAID`, `CANCELED`, `PARTIAL_REFUNDED` …) used to be here and
was removed with the gateway.

Paymob reports outcomes as booleans, which the adapter reduces to one token first;
the order of those checks matters, because a refunded transaction also reports
`success: true`.

| Paymob transaction | Adapter token | Ours |
|---|---|---|
| `is_refunded` | `refunded` | `refunded` |
| `is_voided` | `voided` | `cancelled` |
| `pending` | `pending` | `requires_action` |
| `success` + `is_auth` without `is_capture` | `authorized` | `authorized` |
| `success` | `captured` | `captured` |
| anything else | `failed` | `failed` |

**`INITIAL_STATUSES`** is the other language: the states one of *our own* adapters
may report when it creates a payment (`pending`, `requires_action`, `authorized`).
Validated by `assert_initial_status`, which raises rather than defaulting to
`pending` — an adapter reporting something unexpected has a bug, and quietly calling
the payment pending would hide it behind a customer who is never sent anywhere to
pay.

Conflating the two was a real bug. An adapter reported `requires_action` on
creation, that value was run through the gateway's *webhook* table, it was not
there, and checkout 500ed on a `KeyError`.

## Order status versus payment status

They are separate, and a COD order is the reason.

A cash-on-delivery order is `CONFIRMED` while its payment is still `pending`: the
order is real, the stock is committed, the customer has been emailed — and nobody
has collected any money. Conversely a card order stays `PENDING` while its payment
is `requires_action`, because stock must not be consumed for an order nobody has
paid for.

`checkout.services.confirm_order(order, payment_received=False)` is the COD case.
An InstaPay order is stricter still: it is not confirmed *at all* until the transfer
is confirmed, because nothing about it has been verified.
`confirm_order_after_payment(order)` is everything else, and additionally sends the
"payment received" email — which the COD path must not, because no payment has
been received.
