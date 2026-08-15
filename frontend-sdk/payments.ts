/**
 * Payment endpoints — apps/payments/urls.py.
 *
 * The backend explicitly documents (apps/payments/views.py) that the
 * frontend must never mark a payment successful itself based on redirect
 * query params — GET /payments/{id}/status/ is the only source of truth.
 *
 * Note: POST /payments/{id}/refund/ is admin-only (IsAdminUser) in the
 * current backend and is intentionally not exposed here — it is not usable
 * from a customer-facing storefront.
 */
import { apiGet } from "./api-client";
import type { Payment, PaymentStatus } from "./types";

/** GET /payments/{id}/ */
export async function getPayment(id: number): Promise<Payment> {
  return apiGet<Payment>(`/payments/${id}/`);
}

/**
 * GET /payments/{id}/status/ — polls the provider (if it supports live
 * status polling; otherwise relies on webhooks already having updated it)
 * and returns the current Payment.
 */
export async function getPaymentStatus(id: number): Promise<Payment> {
  return apiGet<Payment>(`/payments/${id}/status/`);
}

const SETTLED_STATUSES: PaymentStatus[] = ["captured", "failed", "cancelled", "expired"];

export interface PollPaymentStatusOptions {
  /** Delay between polls, in ms. Default 2000. */
  intervalMs?: number;
  /** Give up after this long, in ms, and return the last known status. Default 120000 (2 min). */
  timeoutMs?: number;
  /** Called with each intermediate status while polling. */
  onUpdate?: (payment: Payment) => void;
}

/**
 * Polls GET /payments/{id}/status/ until the payment reaches a settled
 * status (captured/failed/cancelled/expired) or the timeout elapses.
 * Use this right after redirecting the user back from a card/Fawry/wallet
 * gateway — see docs/frontend/README.md -> "Recommended polling pattern".
 */
export async function pollPaymentStatus(
  id: number,
  options: PollPaymentStatusOptions = {}
): Promise<Payment> {
  const { intervalMs = 2000, timeoutMs = 120000, onUpdate } = options;
  const deadline = Date.now() + timeoutMs;

  let payment = await getPaymentStatus(id);
  onUpdate?.(payment);

  while (!SETTLED_STATUSES.includes(payment.status) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    payment = await getPaymentStatus(id);
    onUpdate?.(payment);
  }

  return payment;
}
