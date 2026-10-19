# Payment providers

Start here:

```bash
python manage.py check_payments
```

It prints, per method, whether it is live and exactly which setting is empty if it
is not — plus the webhook URL to register with Paymob.

---

## What this shop accepts

Four methods. Fawry, valU, Sympl and Aman were implemented and then **removed** at
the business's request; an adapter for a method nobody sells is an adapter nobody
tests, and the model choices are gone too.

| Method | Route | Code | Verified against the provider | Live |
|---|---|---|---|---|
| Cash on delivery | — | complete | n/a — no external service | **yes** |
| InstaPay transfer | manual | complete | n/a — InstaPay has no API | **yes**, once configured |
| Card (Visa / Mastercard / Meeza) | Paymob | complete | **no** — no merchant account | no |
| Mobile wallet (Vodafone Cash, Etisalat, Orange, WE) | Paymob | complete | **no** — no merchant account | no |
| InstaPay | Paymob | complete | **no** — Paymob lists it *coming soon* | no |

**"Code complete" means** request building, signature generation, signature
verification, status mapping, reconciliation, refund and cancellation are
implemented against Paymob's current official documentation and tested against
fixtures of the documented shapes.

**It does not mean the gateway works.** No Paymob environment has ever answered
this code, because reaching even their sandbox requires merchant onboarding this
project has not done. Cash on delivery and InstaPay are the two methods that can
honestly be described as live today, and neither involves a gateway.

Nothing external is enabled by default, and an enabled-but-incomplete gateway
stays hidden rather than half-working — see `apps/payments/config.py`.

---

## Methods, routes, and why InstaPay has two

A **method** is what the customer picks. A **route** is what settles it.

* `card` and `mobile_wallet` → Paymob.
* `cod` → nothing; the courier collects.
* `instapay` → **two possible routes**, and the better available one wins
  automatically:
  1. **Paymob**, if `PAYMOB_INTEGRATION_ID_INSTAPAY` is set. Paymob lists InstaPay
     as coming soon, so this is empty today.
  2. **Manual transfer**, otherwise. The customer transfers from their banking app
     and staff confirm. This is what works now.

`config.availability()` picks between them, so enabling the Paymob integration ID
switches the route with no code change and no window where InstaPay is offered
twice. Details and the operating instructions: [instapay.md](instapay.md).

---

## Cash on delivery

The method the shop runs on, and the only one whose behaviour changed for
correctness rather than being added. Full detail: [cod.md](cod.md).

**A COD payment is `pending` from checkout until somebody records collection.** It
used to be marked `captured` the moment the order was placed — before a courier had
left the building. The books said the money was ours when it was not, and because
`captured` is the state refunds are issued from, a cancelled COD order could be
"refunded" cash nobody had handed over.

The order is still confirmed at checkout (stock committed, confirmation email
sent); only the *payment* waits, and the "payment received" email does not go out
because no payment has been received.

| Setting | Meaning |
|---|---|
| `COD_ENABLED` | Offer it at all. |
| `COD_FEE_AMOUNT` | Collection surcharge, added to the total. |
| `COD_MIN_ORDER_AMOUNT` | Below this, COD is refused. Checked before the fee is added. |
| `COD_MAX_ORDER_AMOUNT` | Above this, COD is refused. `0` means no ceiling. |
| `COD_ALLOWED_SHIPPING_METHOD_IDS` | Comma-separated. Empty means any carrier. |

Collection happens automatically when an order moves to `DELIVERED`, by any route —
the admin API, the Django admin, or a courier integration added later. The hook is
on the status transition, not on one dashboard button. Staff can also record it
explicitly, including a refusal at the door:

```
POST /api/v1/payments/{id}/settle/            {"outcome": "received"|"refused", "note": "..."}
POST /api/v1/admin-api/payments/{id}/settle/  (same, from the dashboard)
```

Refunds are recorded, not executed: somebody hands the cash back or issues store
credit. The dashboard says so rather than offering a button that pretends
otherwise.

---

## Vodafone Cash without a gateway

Until Paymob's wallet integration is configured, the mobile wallet method can be
offered as a transfer to the shop's Vodafone Cash number that staff confirm, set
up exactly like InstaPay. See `vodafone-cash.md`.

## Paymob

One gateway, three methods: card, mobile wallet, and InstaPay when it arrives. The
integration ID chosen by the customer's method is the only difference between them.

### Credentials

From **Dashboard → Settings → Account Info**.

| Variable | What it is |
|---|---|
| `PAYMOB_SECRET_KEY` | Authenticates the Intention API (`Authorization: Token …`). Creates payments. |
| `PAYMOB_PUBLIC_KEY` | Builds the hosted checkout URL the customer is sent to. |
| `PAYMOB_API_KEY` | **A different credential.** The older Accept API key, exchanged for a short-lived token. Refunds, voids and status inquiries need it; nothing else does. |
| `PAYMOB_HMAC_SECRET` | Verifies every callback. Mandatory — without it we could take money and have no trustworthy way to learn that we had. |
| `PAYMOB_INTEGRATION_ID_CARD` | Visa, Mastercard, Meeza. |
| `PAYMOB_INTEGRATION_ID_WALLET` | Vodafone Cash, Etisalat, Orange, WE. |
| `PAYMOB_INTEGRATION_ID_INSTAPAY` | InstaPay, when Paymob releases it. Empty today. |

A missing integration ID hides **that method only**. Card can be live while wallet
is not. A missing `PAYMOB_API_KEY` leaves every method live but refunds
unavailable, which the dashboard shows per payment.

`PAYMOB_LIVE` does not change any URL — Paymob switches environment by key — but it
is recorded on every payment, so a live transaction created with a test key is
visible rather than merely wrong.

### Webhook to register

**Dashboard → Settings → Payment Integrations → Transaction Processed Callback:**

```
https://<your-domain>/api/v1/payments/webhooks/paymob/
```

Register the **Transaction Processed Callback** (server-to-server, POST, JSON).
The **Transaction Response Callback** is the browser redirect and is deliberately
*rejected* by this endpoint: its fields are flattened and it arrives through the
customer's browser, so accepting it would let a customer confirm their own order.
Point the response callback at the storefront instead:

```
https://<storefront-domain>/order/payment-return
```

### HMAC

HMAC-SHA512 over 20 transaction fields concatenated in a fixed order with no
separators, hex-encoded, delivered as the `hmac` query parameter and also as a
top-level body key. The order is the specification — see `HMAC_TRANSACTION_FIELDS`
in `apps/payments/providers/paymob.py`, and do not reorder it. Getting it wrong
does not fail loudly; it produces a digest that never matches, and "fixing" that by
comparing something shorter produces one that matches forged events.

One bug worth recording: the webhook view originally passed
`dict(request.query_params)`. A `QueryDict` is a `MultiValueDict`, so that yields
`{"hmac": ["abc…"]}` — a list, which stringifies to `"['abc…']"` and can never
match a digest. Every genuine Paymob callback would have failed verification. It is
`request.query_params.dict()`.

### Endpoints used

| Operation | Method and path |
|---|---|
| Create payment | `POST /v1/intention/` |
| Accept auth token | `POST /api/auth/tokens` |
| Refund | `POST /api/acceptance/void_refund/refund` |
| Void | `POST /api/acceptance/void_refund/void` |
| Status inquiry | `POST /api/ecommerce/orders/transaction_inquiry` |
| Hosted checkout | `GET /unifiedcheckout/?publicKey=…&clientSecret=…` |

### Still required before this can be called live

1. Merchant account and Egyptian commercial registration accepted by Paymob.
2. Card and wallet approved individually on the account.
3. Sandbox credentials, then a test transaction per enabled method with the test
   cards from Paymob's own documentation.
4. Callback registered, and a real callback received and verified.
5. A refund and a void executed in sandbox.
6. Production keys, and `PAYMOB_LIVE=True`.

Paymob's merchant onboarding is also what would make InstaPay-through-Paymob
available; there is nothing to do at this end but set the integration ID.

---

## What the payment layer guarantees regardless of route

Enforced in `apps/payments/services.py` and `apps/payments/state.py`, not in any
adapter:

* **The amount and currency are ours.** Computed from the cart, written onto the
  Payment, and every later claim about them — a webhook's amount, a
  reconciliation's total — is checked against that value rather than replacing it.
  A mismatch is refused and recorded.
* **A status only changes through the state machine.** A captured payment cannot be
  walked back to failed by a replayed event.
* **Only a verified webhook, a server-to-server reconciliation, or a staff member
  on a manually-settled route may mark an order paid.** Never a browser returning
  from a gateway.
* **Duplicate work is a no-op.** The webhook table is unique on the provider's
  event id, refunds on their idempotency key, payments on the checkout key.
* **Refunds are capped, staff-only, idempotent and audited.**
* **Secrets never reach a log or a stored payload** — HMACs, signatures, PANs and
  API keys are redacted before storage (`apps/core/logging_filters.py`).

[FLOWS.md](FLOWS.md) walks the happy path and the failure paths;
[STATES.md](STATES.md) has the state table.
