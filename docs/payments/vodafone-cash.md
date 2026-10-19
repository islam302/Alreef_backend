# Vodafone Cash

The "mobile wallet" payment method has two routes, and the shop uses whichever is
configured, preferring the first:

1. **Paymob** (automatic). With `PAYMOB_INTEGRATION_ID_WALLET` set, the customer
   gets a payment request on their phone and Paymob confirms it to us with a
   signed callback. Covers Vodafone Cash and the other Egyptian wallets. Paymob
   charges a fee per transaction. See `PROVIDERS.md`.
2. **Transfer to the shop's wallet** (manual, `apps/payments/providers/vodafone_cash.py`).
   The customer sends the amount from their own wallet to the shop's Vodafone Cash
   number and writes the order number in the transfer; staff confirm it arrived.
   No gateway and no fee.

Configuring Paymob's wallet integration switches the method to route 1 on its own;
route 2 then stops being offered. Nothing else changes.

## What the customer sees

Checkout lists «فودافون كاش». After placing the order they see the wallet number
(large, copyable), the exact amount and the order number to write in the transfer
message, and receive the same details by email. The order page and the
"track your order" page keep showing them until the payment is confirmed. Guests
(checkout without an account) can use it: nothing redirects them anywhere.

## The risk, stated plainly

Nothing verifies that money arrived except a person. So, exactly as with InstaPay:

- the payment stays `requires_action` and the order stays unconfirmed;
- the stock is reserved, not consumed;
- the order is **not** dispatched until staff record the transfer as received.

## Configuration

| Variable | What it is |
|---|---|
| `VODAFONE_CASH_ENABLED` | Offer it at all. |
| `VODAFONE_CASH_NUMBER` | The shop's Vodafone Cash wallet number, e.g. `010xxxxxxxx`. **Mandatory** — without it the method is not offered. |
| `VODAFONE_CASH_ACCOUNT_NAME` | The wallet owner's name as the sender's app shows it, so a cautious customer can check it before sending. |
| `VODAFONE_CASH_CONFIRM_WINDOW_HOURS` | How long an order waits before staff chase it. Default 24. |

None of these is a secret: a number to pay into is shown to customers on purpose.

## Operating it

Dashboard → المدفوعات, filter «محتاجة إجراء»: the queue of orders waiting on a
transfer. Open the Vodafone Cash app, find the incoming transfer, then on the row
choose «أيوه، وصل» (records the payment, confirms the order, emails the customer)
or «التحويل ما وصلش».

- **Wrong amount:** do not confirm. Nothing here can check it, so confirming a short
  transfer records money the shop does not have. Refuse it and contact the customer.
- **No order number in the message:** common. Match on the amount, the time and the
  sender's number against the order's phone.
- **Refunds** are a transfer back from the shop's wallet, then recorded from the
  payment's refund button (marked manual).
