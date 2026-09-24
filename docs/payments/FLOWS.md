# Payment flows

Five paths, and the failure paths matter more than the happy one.

Provider setup is in [PROVIDERS.md](PROVIDERS.md); the state table is in
[STATES.md](STATES.md).

---

## 1. Cash on delivery

```
POST /checkout/            Idempotency-Key required
  -> Order CONFIRMED, stock consumed, confirmation email
  -> Payment PENDING                       <- not captured
  -> response carries the amount to have ready

... courier delivers ...

Order -> DELIVERED  (admin API, Django admin, any route)
  -> Payment CAPTURED, "payment received" email, loyalty points
```

The payment is `pending` for the whole middle of that, and that is the point: the
money is not ours until somebody collects it. Staff can also record the outcome
directly, including a refusal at the door, via
`POST /payments/{id}/settle/ {"outcome": "received"|"refused"}`.

## 2. A redirect gateway (Paymob: card, mobile wallet)

```
POST /checkout/
  -> Order PENDING                         <- not confirmed
  -> Payment REQUIRES_ACTION, metadata.redirect_url
  -> storefront sends the customer to Paymob

customer pays

Paymob -> POST /payments/webhooks/paymob/   (server to server, HMAC signed)
  -> verify signature, amount, currency, merchant reference, freshness
  -> Payment CAPTURED
  -> Order CONFIRMED, stock consumed, emails

Paymob -> customer's browser -> /order/payment-return?payment=<id>
  -> storefront polls /payments/{id}/status/ until settled
```

The two arrows from Paymob are independent and the order between them is not
guaranteed. The customer frequently arrives back *before* the webhook. That is why
the return page polls instead of concluding anything, and why it reads only the
payment id from the URL — never a success flag, which the customer's browser
controls.

## 3. InstaPay

```
POST /checkout/
  -> Order PENDING
  -> Payment REQUIRES_ACTION
  -> response carries the shop's InstaPay address, the amount and the reference

... customer transfers from their own banking app, on their own schedule ...

staff -> POST /admin-api/payments/{id}/settle/  {"outcome": "received"}
  -> Payment CAPTURED, Order CONFIRMED
```

No redirect, no webhook, and nothing to poll: InstaPay publishes no merchant API,
so a person confirms. The order stays `PENDING` and its stock stays merely reserved
until they do — an InstaPay order is **not** fulfilled on a promise the way a COD
order is. See [instapay.md](instapay.md).

The customer can close the tab. The address and the reference are on the order page
and in their email.

## 4. The webhook never arrives

The failure this system is built around, because it is the common one.

```
Payment stuck in REQUIRES_ACTION, Order stuck in PENDING, customer insists they paid

Either:
  customer's own polling         GET  /payments/{id}/status/
  or staff                       POST /admin-api/payments/{id}/reconcile/

  -> ask the provider server-to-server
  -> map its answer through PROVIDER_STATUS_MAP
  -> check the amount agrees with ours
  -> check the transition is legal
  -> apply it, and confirm the order
```

Reconciliation is queried by *our* merchant reference, so it does not depend on
anything the customer told us. An amount that disagrees raises rather than
settling: unlike a webhook mismatch, a wrong reconciliation is a silent loss that
nothing else would flag.

## 5. A refund

```
POST /admin-api/payments/{id}/refund/   staff only, Idempotency-Key
  -> refuse unless the payment is CAPTURED or PARTIALLY_REFUNDED
  -> refuse unless this method's gateway can refund (or say it is manual)
  -> refuse above the remaining balance
  -> Payment REFUND_PENDING, Refund row written
  -> call the gateway
  -> Payment REFUNDED or PARTIALLY_REFUNDED; on failure, back to CAPTURED
  -> audit log, customer email
```

The Refund row is written *before* the gateway is called, for the same reason a
Payment is: if the call times out, a refund may well have happened, and a row is
what lets somebody find out.

---

## What a webhook has to survive

`process_webhook` fails closed at every step, in this order. Each row is a test in
`tests/test_payments_webhooks.py`.

| Condition | Answer | Recorded? |
|---|---|---|
| Signature does not verify | **400** | No — see below |
| Body unreadable / no event id | **400** | No |
| Duplicate event id | **200** | Already was |
| No payment matches the references | **202** | Yes, unattributed |
| Merchant reference is somebody else's | **409** | Yes, rejected |
| Amount disagrees with ours | **409** | Yes, rejected |
| Currency disagrees with ours | **409** | Yes, rejected |
| Event older than `PAYMENT_WEBHOOK_MAX_AGE_HOURS` | **202** | Yes, rejected |
| Status we have no mapping for | **202** | Yes, rejected |
| Status carries no change (an order-created notification) | **200** | Yes, processed |
| Transition is illegal (out-of-order) | **202** | Yes, rejected |
| Everything checks out | **200** | Yes, applied |

**Nothing is recorded for a bad signature**, deliberately. The payload is
unattributed attacker-controlled data, and `provider_event_id` is unique — so
storing it would let anyone occupy an id and block the genuine event that follows.
A denial of service on our own settlement.

**202 means "stop retrying".** Providers retry until they get a 2xx, and an event
about somebody else's account will never apply no matter how many times it
arrives. 409 is the one worth alerting on: a correctly signed event that
contradicts our record means two systems disagree about money.

---

## Idempotency, at three layers

A customer double-tapping *pay* on a flaky connection must not buy twice.

1. **`Idempotency-Key` + the cache** (`apps/core/idempotency.py`) — the fast path.
   The same key and body returns the stored response; a concurrent duplicate gets
   409 rather than executing twice.
2. **`Payment.idempotency_key`, unique** — the path when the cache was flushed
   between attempts, which is exactly when a naive retry would charge twice.
3. **`PaymentWebhookEvent.provider_event_id`, unique** — the provider's own
   retries.

Plus `Refund.idempotency_key` for payouts, and re-entering the same state being
always legal, which is what makes a duplicated event harmless rather than an
error.

Order confirmation is reachable more than once for the same order (a COD order
confirms at checkout and again at collection), so its side effects are idempotent
too: `record_usage` for coupons and `award_points_for_order` for loyalty both
refuse to run twice. Before that they did not, and a COD order would have spent
two units of a coupon's limit and paid out points twice.
