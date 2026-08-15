/**
 * Checkout endpoints — apps/checkout/urls.py.
 *
 * POST /checkout/ requires an `Idempotency-Key` header (apps/core/idempotency.py):
 * generate one per logical checkout attempt (crypto.randomUUID()) and reuse it
 * across network-failure retries of the *same* attempt; mint a new one if the
 * user changes cart/address/etc. and checks out again.
 */
import { apiPost, newIdempotencyKey } from "./api-client";
import type { CheckoutInput, CheckoutResult, CheckoutTotals } from "./types";

/**
 * POST /checkout/summary/ — preview only, does not touch the cart or create
 * anything. Call whenever address/shipping method/coupon changes so the
 * displayed total always matches the server (which owns all pricing logic).
 */
export async function getCheckoutSummary(input: CheckoutInput): Promise<CheckoutTotals> {
  return apiPost<CheckoutTotals>("/checkout/summary/", input);
}

/**
 * POST /checkout/ — places the order. Pass an idempotencyKey to reuse one
 * across retries of the same attempt; if omitted, a fresh one is generated
 * (fine for a first attempt, but capture and reuse it yourself if you need
 * to retry after e.g. a network timeout, so the retry replays the same
 * result instead of risking a second order).
 */
export async function createCheckout(
  input: CheckoutInput,
  idempotencyKey: string = newIdempotencyKey()
): Promise<CheckoutResult> {
  return apiPost<CheckoutResult>("/checkout/", input, {
    headers: { "Idempotency-Key": idempotencyKey },
  });
}
