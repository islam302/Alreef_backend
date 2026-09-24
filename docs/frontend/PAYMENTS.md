# Checkout & payments

The full path from a filled cart to a paid order, what the backend does at each step,
and — importantly — which payment methods actually work today.

## Ask the server which methods exist

```
GET /api/v1/payments/methods/          public, unpaginated
```

```json
{"success": true, "data": [
  {"code": "cod", "label_ar": "الدفع عند الاستلام", "label_en": "Cash on delivery",
   "instructions_ar": "تدفع قيمة الطلب كاش للمندوب وقت التسليم.",
   "redirects": false, "sort_order": 10, "cod_fee": "15.00"}
]}
```

**Do not keep a local list.** This document used to tell you to:

```ts
export const ENABLED_PAYMENT_METHODS: PaymentMethod[] = ["cod"];
```

That was the wrong advice and the storefront followed it. Two copies of "which
gateways work" drift, and they drift the dangerous way round: the shop keeps
offering a method after its credentials are pulled, and the customer finds out at
the last step, after entering an address. The endpoint above is the single answer,
and it carries its own Arabic labels and instructions so the wording lives with the
fact rather than being copied into two frontends.

A method appears there only when its gateway is enabled **and** every credential
its adapter will reach for is present. It never says *why* a method is missing —
that names our configuration. Staff get the reasons from
`GET /admin-api/payment-methods/`.

Render whatever comes back, in `sort_order`. If the array is empty, no payment is
possible: say so and offer the phone number rather than showing an empty picker.

### Which route serves which method

| `code` | Route | Notes |
|---|---|---|
| `cod` | — | Live. Carries `cod_fee`, which is already in the checkout total. |
| `instapay` | manual transfer | Live. No redirect: the customer transfers from their own banking app and staff confirm. |
| `card` | Paymob | Visa, Mastercard, Meeza. `redirects: true`. |
| `mobile_wallet` | Paymob | Vodafone Cash, Etisalat, Orange, WE. `redirects: true`. |

That is the whole list. Fawry, valU, Sympl and Aman were removed — if you find a
reference to them in older frontend code, delete it.

`cod` and `instapay` are live today; the two Paymob methods need merchant
onboarding. See `docs/payments/PROVIDERS.md`.

**InstaPay may switch route without notice.** Today it is the manual transfer;
when Paymob releases their InstaPay integration the same `code` starts arriving
with `redirects: true` and a `redirect_url`. Branch on the *shape* of what you get
(is there a `redirect_url`? is there a `pay_to`?) rather than on the method code,
and both work with no change.

## Unavailable methods

Picking a method that is not offered returns **400
`PAYMENT_METHOD_NOT_AVAILABLE`** from `POST /checkout/summary/` and
`POST /checkout/`, with a customer-safe message. It used to be a 503 raised late;
it is now raised in `calculate_totals`, before an order row exists, so there is
nothing to clean up. The error never names a setting.

The whole of `create_order_from_cart` runs in one transaction, so a gateway that
fails mid-checkout rolls the order back entirely — no orphaned `pending` order, and
the cart is untouched.


The rest of this document describes both paths, marking which is live.

---

## Step 1 — Preconditions

Before the checkout page can call anything, you need:

1. **An authenticated user.** Both checkout endpoints are `IsAuthenticated`; there is no
   guest checkout. Log the user in (or register + log in) first, then merge the guest
   cart — see the merge flow in [`README.md`](./README.md#guest-cart--user-cart-merge-flow).
2. **A non-empty cart** — otherwise `400 CART_EMPTY`.
3. **An `address_id`** from `GET /addresses/` (or a freshly created one).
4. **A `shipping_method_id`** from `GET /shipping/quotes/?governorate=<code>&subtotal=<n>`.
   Quote by the governorate **code** of the selected address.

## Step 2 — Preview totals

```http
POST /api/v1/checkout/summary/
{ "address_id": 12, "shipping_method_id": 3, "payment_method": "cod", "coupon_code": "SAVE10" }
```

```jsonc
{
  "success": true,
  "data": {
    "subtotal": "480.00",
    "coupon_discount": "48.00",
    "promotion_discount": "0.00",
    "tax_total": "60.48",
    "shipping_total": "35.00",
    "cod_fee": "15.00",
    "grand_total": "542.48",
    "currency": "EGP",
    "free_shipping": false
  }
}
```

This creates nothing and mutates nothing. Call it on **every** change to address,
shipping method, coupon or payment method — the payment method matters because `cod_fee`
(default 15.00 EGP) only applies to COD, so the displayed total changes when the user
switches methods.

**The frontend never computes money.** VAT (14% on `standard`-tax products only),
promotion stacking and shipping rates all live server-side. Render what `summary`
returns, in the order above.

A few totals arithmetic notes, so your line items add up:

- `grand_total` = `subtotal` − `coupon_discount` − `promotion_discount` + `tax_total` +
  `shipping_total` + `cod_fee`, floored at zero for the discounted subtotal.
- `tax_total` is computed on the **undiscounted** line totals.
- `free_shipping: true` means a promotion or a `free_shipping` coupon zeroed the shipping
  line — `shipping_total` will already be `"0.00"`. Show the original quote struck
  through if you want to make the saving visible.
- Every amount is a **decimal string**. Parse deliberately; don't let JS floats near
  them. See [`I18N.md`](./I18N.md#money).

## Step 3 — Place the order

```http
POST /api/v1/checkout/
Idempotency-Key: 3f2b9c7e-...          <-- required, 400 IDEMPOTENCY_KEY_REQUIRED without it
{ "address_id": 12, "shipping_method_id": 3, "payment_method": "cod",
  "coupon_code": "SAVE10", "notes": "Leave at the door" }
```

Returns `201` with:

```jsonc
{
  "data": {
    "order": { /* full OrderDetail */ },
    "payment": {
      "id": 88,
      "provider": "cod",
      "method": "cod",
      "status": "pending",              // NOT captured — see step 4a
      "redirect_url": "",               // a gateway URL, when the customer must leave
      "iframe_url": "",
      "provider_reference": "COD-4F2A9C1B77E3",
      "instructions_ar": "تدفع قيمة الطلب كاش للمندوب وقت التسليم.",
      "pay_to": "",                     // an InstaPay address, when the customer transfers
      "pay_reference": ""               // what to quote with that transfer
    }
  }
}
```

The `payment` object here is a hand-built dict, **not** the `Payment` serializer, and
it is deliberately narrow: enough to send the customer onward and nothing else. No
provider payload, and nothing that would let the frontend decide the order is paid —
that verdict comes only from `GET /payments/{id}/status/`. The full `Payment` (with
`amount`, `metadata`, `failure_reason`, `order_number`) comes from
`GET /payments/{id}/`.

Server-side, in one transaction: the cart converts to an `Order`, stock is reserved per
line, a `Payment` row is created, and the cart is marked `converted`. If anything fails,
all of it rolls back.

Idempotency rules are in [`ERROR-HANDLING.md`](./ERROR-HANDLING.md#checkout-the-one-flow-worth-getting-exactly-right)
— read that before writing the submit handler. The short version: one key per attempt,
reuse it across retries of that attempt, mint a new one if the user edits anything.

## Step 4a — COD *(live)*

COD has no gateway round trip, so the response you already have is the whole answer.
**Go straight to the confirmation page — do not poll.**

```
order.status   == "confirmed"      the order is real, stock is committed
payment.status == "pending"        nobody has collected any cash yet
```

**This changed, and the old behaviour is worth knowing about.** Checkout used to call
`mark_payment_captured` inline, so `payment.status` came back `captured` — which said
the money was ours before a courier had left the building, and (because `captured` is
the state refunds are issued from) allowed a cancelled COD order to be "refunded" cash
nobody had handed over. If your code branches on `payment.status === "captured"` to
show a COD success screen, it needs to accept `pending` too.

What checkout does now, in one transaction: converts the cart, reserves and consumes
stock, confirms the order, records coupon usage, awards loyalty points, and queues the
**order-confirmation** email — but not the payment-received one, because no payment has
been received. The payment becomes `captured` when the order reaches `DELIVERED`, or
when staff record collection explicitly.

Word the confirmation screen as an instruction, not a receipt: "جهّز {{amount}} كاش
للمندوب وقت التسليم". The total already includes the COD fee, and
`payment.instructions_ar` carries that sentence if you would rather not hard-code it.

## Step 4b — Redirect gateways, and InstaPay

Two shapes, and they are genuinely different for the UI.

**A redirect gateway** (card and mobile wallet, both Paymob) returns
`payment.status: "requires_action"` and a `redirect_url`. Send the customer there,
after the order number is on screen and with a visible link that does the same thing:
a popup blocker or a back button must never leave somebody with an unpaid order and no
way to reach the gateway. They come back to `/order/payment-return?payment={id}`.

**InstaPay** returns `requires_action` with no redirect at all, and instead a
`pay_to` (the shop's InstaPay address) and a `pay_reference` (the order number to
quote in the transfer note). The customer types the address into their own banking
app. The address is the single most important thing on the screen — it is the only
way they can pay — so show it large, selectable and in LTR. They can close the tab;
the payment is confirmed later by a person, not by a webhook, so do not poll for it
and do not make them wait.

The storefront implements both in `src/components/PaymentNextStep.jsx`, which the
confirmation screen and the payment-return page share.

The steps for a redirect gateway are:

1. Read `payment.redirect_url` (full-page redirect) or `payment.iframe_url` (embedded
   Paymob iframe). Whichever the provider populates.
2. Send the user there. Keep `payment.id` and `order.order_number` somewhere durable —
   `sessionStorage` or, better, the return URL's query string — because a full-page
   redirect wipes in-memory state.
3. The user comes back to your return URL with provider-specific query params.
4. **Ignore those query params for anything that matters.** They are attacker-controlled.
   Poll `GET /api/v1/payments/{id}/status/` instead; that endpoint re-checks with the
   provider server-side and is the only authority on whether money moved. The backend
   documents this explicitly in `apps/payments/views.py::PaymentStatusView`.
5. Meanwhile the gateway also calls `POST /api/v1/payments/webhooks/{provider}/`
   server-to-server, which is what actually flips the payment to `captured` and confirms
   the order. Polling just lets you observe it.

### The polling loop

```ts
// Every state that will not change again. `requires_action` is NOT here: that is
// the customer still owing us an action, which is exactly what we are waiting on.
const TERMINAL: PaymentStatus[] = [
  "captured", "failed", "cancelled", "expired", "refunded", "partially_refunded",
];

export async function pollPaymentStatus(
  paymentId: number,
  { intervalMs = 2000, timeoutMs = 120_000 } = {}
): Promise<Payment> {
  const deadline = Date.now() + timeoutMs;
  let payment: Payment;
  do {
    const res = await api.get(`/payments/${paymentId}/status/`);
    payment = res.data.data;
    if (TERMINAL.includes(payment.status)) return payment;
    await new Promise((r) => setTimeout(r, intervalMs));
  } while (Date.now() < deadline);
  return payment; // still pending — see below
}
```

There is a working version of this in
[`../../frontend-sdk/payments.ts`](../../frontend-sdk/payments.ts).

**On timeout, do not show "payment failed."** The webhook may still be in flight. Show
"We're confirming your payment" with a link to the order page, and let the order list be
the source of truth. A false "failed" makes customers pay twice.

Back off the interval if you poll for long — 2s is fine for the first 30s, then stretch
it. This endpoint has its own throttle scope, `payment_status` at **60/min**, because
each call can reach a provider; it is not the generic 300/min `user` budget. A tight
loop across several tabs will trip `429`.

### What each terminal status should show

| `payment.status` | Screen |
|---|---|
| `captured` | Success. Show the order number and a link to `/orders/{order_number}`. |
| `failed` | "Payment didn't go through." The order still exists, unpaid — offer to retry or switch method. |
| `cancelled` | The user backed out at the gateway. Return them to checkout with the cart intact. |
| `expired` | The payment window closed — a lapsed checkout intention, or an InstaPay transfer never made. Offer a fresh attempt. |
| still `pending` after timeout | "We're confirming your payment." Never "failed". |

## Step 5 — After the order exists

- `GET /orders/{order_number}/` — the order page. Lookup is by `order_number`, not `id`.
- `order.status_history` drives the progress timeline.
- Cancel and return eligibility rules are in [`STATES.md`](./STATES.md#which-actions-to-show).
- Refunds are **admin-only** (`POST /payments/{id}/refund/`, `IsAdminUser`). A customer
  requests a return instead; an admin turns that into a refund. Don't build a customer
  refund button.

## Things that will bite you

**COD is gated by settings.** If `COD_ENABLED=False`, even `/checkout/summary/` fails
with `503 PAYMENT_PROVIDER_UNAVAILABLE` (the fee lookup happens during totals
calculation). Handle that on the summary call, not just on submit.

**The COD fee is server-side config** (`COD_FEE_AMOUNT`, default `15.00`). Read it from
`summary.cod_fee`; never hardcode 15 in the frontend.

**Loyalty points, gift cards and store credit cannot be applied at checkout.**
`CheckoutInput` has no field for them, so `order.loyalty_discount`,
`gift_card_amount_used` and `store_credit_amount_used` always come back `"0.00"`.
`POST /loyalty/redeem/` and `POST /gift-cards/check/` compute a value but nothing
consumes it. Don't build "pay with points" UI against the current API.

**Stock is reserved, not held indefinitely.** Reservations expire and a Celery beat task
releases them every 5 minutes. A user who sits on a payment page for a long time can come
back to `409 RESERVATION_EXPIRED` — send them back to the cart rather than retrying
blindly.

**`payment.metadata`** is where provider extras land (`instructions` for COD, and
whatever a gateway returns later). `redirect_url` and `iframe_url` are always present as
keys, empty when unused — check for truthiness, not for the key.

## Related

- [`ERROR-HANDLING.md`](./ERROR-HANDLING.md) — idempotency and the payment error codes.
- [`STATES.md`](./STATES.md#payment-status) — every payment and order status.
- [`../payments/`](../payments/) — per-route backend notes (`paymob.md`, `instapay.md`,
  `valu.md`, `cod.md`).
- [`../architecture/payments.md`](../architecture/payments.md) — server-side sequence
  diagrams.
