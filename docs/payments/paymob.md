# Paymob

**Status: implemented against Paymob's current official documentation; never run
against a Paymob environment.** Disabled by default.

The canonical page is **[PROVIDERS.md](PROVIDERS.md)** — credentials, the webhook
to register, the endpoints used, the HMAC rules, and the checklist of what merchant
onboarding still has to happen before this can be called live. This file exists so
that a link to `paymob.md` still lands somewhere true.

`apps/payments/providers/paymob.py`.

## What changed since this file said "skeleton"

It said every method raised `NotImplementedError` and that no HTTP calls existed.
That was accurate at the time and is not now. Implemented:

* **Create payment** — the Intention API (`POST /v1/intention/`), returning a
  hosted-checkout URL. Amount in minor units, quantised half-up so an exact-half
  amount cannot undercharge.
* **Webhook verification** — HMAC-SHA512 over the documented 20-field
  concatenation, compared with `hmac.compare_digest`.
* **Status classification** — Paymob's `success`/`pending`/`is_voided`/`is_refunded`
  booleans reduced to one token, terminal outcomes tested first because a refunded
  transaction also reports `success: true`.
* **Refund and void** — the Accept API, which authenticates with `PAYMOB_API_KEY`,
  a *different* credential from `PAYMOB_SECRET_KEY`.
* **Status inquiry** — queried by our own merchant reference, so a lost callback is
  recoverable without trusting anything the customer's browser said.

## One gateway, three methods

Card (Visa, Mastercard, Meeza) and mobile wallet (Vodafone Cash and the other
Egyptian wallets) today, and InstaPay when Paymob releases it. All are integration
IDs on the same account, sharing this adapter, this HMAC verification and this
refund endpoint; the customer's chosen method picks the integration ID and nothing
else differs.

Fawry, valU, Sympl and Aman were implemented and then removed at the business's
request. InstaPay is served by a manual transfer route until Paymob's integration
lands — [instapay.md](instapay.md).

## What is not proven

No Paymob environment, sandbox or live, has ever answered this code. The contracts
come from the documentation cited in the module docstring and are tested against
fixtures of those documented shapes — which catches a signature computed over the
wrong fields or an amount in the wrong unit, and cannot catch the documentation
being out of date. That check needs sandbox credentials.
