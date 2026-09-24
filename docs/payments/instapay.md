# InstaPay

**Status: live, as a bank transfer the customer makes and a staff member
confirms.** Disabled until `INSTAPAY_ENABLED=True` and `INSTAPAY_ADDRESS` are set.

`apps/payments/providers/instapay.py`.

---

## Why there is no API, and why that is the honest answer

InstaPay is the Instant Payment Network run by the Egyptian Banks Company under
the Central Bank of Egypt. It moves money between bank accounts through banking
apps.

**It is not a payment gateway.** InstaPay's own FAQ describes person-to-person and
person-to-merchant transfers made inside the app. There is no merchant API, no
developer documentation, no merchant onboarding for online acceptance, and no
webhooks. Paymob — the only gateway this project integrates — lists InstaPay as
*coming soon*.

So there is nothing to call. Writing a client anyway would mean inventing an
endpoint and a signature scheme, and a fabricated signature scheme that passes its
own tests is not an integration: it is a way to accept forged payment
notifications.

## What this does instead

The same thing a large number of Egyptian shops do, and it works today:

1. The customer picks InstaPay at checkout.
2. We show them the shop's InstaPay address, the exact amount, and the order
   number to put in the transfer note.
3. They transfer from their own banking app.
4. Somebody at the shop opens their InstaPay app, sees the transfer, and confirms
   it in the dashboard.
5. Only then is the payment `captured` and the order confirmed.

```
POST /api/v1/checkout/            payment_method=instapay
  -> Order PENDING                          <- not confirmed
  -> Payment REQUIRES_ACTION
  -> response carries the address, the amount and the reference

... customer transfers from their banking app ...

POST /api/v1/admin-api/payments/{id}/settle/   {"outcome": "received"}
  -> Payment CAPTURED
  -> Order CONFIRMED, stock consumed, emails sent
```

## The risk, stated plainly

**An InstaPay order must not ship before the transfer is confirmed**, and the code
enforces that: the order stays `PENDING` and the stock stays merely reserved until
somebody posts `received`.

This is the opposite of cash on delivery, which *is* fulfilled on a promise
because the courier collects at the door. If an InstaPay order somehow reaches
`DELIVERED` unpaid, the COD capture hook deliberately does not fire for it —
shipping a parcel is not evidence that a transfer happened
(`tests/test_payments_flows.py::test_delivery_does_not_capture_an_instapay_payment`).

The other consequence: **the confirming staff member is the only evidence the money
arrived.** There is no gateway to corroborate them. So the endpoint is staff-only,
and every confirmation writes an audit entry naming the actor
(`apps/payments/services.py::record_manual_receipt`).

## Configuration

| Variable | What it is |
|---|---|
| `INSTAPAY_ENABLED` | Offer it at all. |
| `INSTAPAY_ADDRESS` | The shop's InstaPay address (IPA), e.g. `alreef@instapay`. **Mandatory** — without it the customer has nowhere to send the transfer, so the method is not offered at all rather than offered and broken. |
| `INSTAPAY_ACCOUNT_NAME` | The account name the customer will see in their banking app, so a cautious customer can check it matches before transferring. |
| `INSTAPAY_CONFIRM_WINDOW_HOURS` | How long an order waits before staff should chase it. Default 24. |

None of these is a secret. An InstaPay address is a published payment address, and
it is shown to customers on purpose.

## Operating it

The dashboard's payments screen filters on `محتاجة إجراء` (`requires_action`),
which is the queue of orders waiting on a transfer. Each row has a confirm action
that asks for the amount and shows the reference the customer was asked to quote.

Two failure modes to train for:

* **The customer transfers the wrong amount.** Do not confirm. The server checks
  nothing here — it cannot — so confirming a short transfer records money we do
  not have. Refuse it and contact the customer.
* **The customer does not quote the reference.** Common. Match on the amount and
  the timestamp, and check the order's phone number against the sender if the app
  shows it.

## When Paymob ships InstaPay

Set `PAYMOB_INTEGRATION_ID_INSTAPAY` and InstaPay becomes an ordinary gateway
payment: hosted checkout, verified HMAC callback, automatic capture, refundable
through Paymob's API. `apps/payments/config.py` prefers that route automatically
as soon as the integration ID is present, and the manual route disappears from
checkout. No code change, and no window where both are offered.

`tests/test_payments_providers.py::test_paymob_serves_instapay_when_its_integration_id_is_configured`
holds that promise.
