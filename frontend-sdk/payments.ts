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
import type { Payment, PaymentMethodOption, PaymentStatus } from "./types";

/**
 * GET /payments/methods/ — the payment methods this shop can actually complete.
 *
 * Public, and the single source of truth. Do not keep a local list of enabled
 * methods: two copies drift, and they drift towards offering a method the server
 * will refuse. Render what comes back, in `sort_order`; an empty array means no
 * payment is currently possible.
 */
export async function getPaymentMethods(): Promise<PaymentMethodOption[]> {
  return apiGet<PaymentMethodOption[]>("/payments/methods/");
}

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

/**
 * Statuses that will not change again.
 *
 * `requires_action` is deliberately absent: that is the customer still owing us an
 * action, which is precisely what the poll is waiting on.
 */
const SETTLED_STATUSES: PaymentStatus[] = [
  "captured",
  "failed",
  "cancelled",
  "expired",
  "refunded",
  "partially_refunded",
];

export interface PollPaymentStatusOptions {
  /** Delay between polls, in ms. Default 2000. */
  intervalMs?: number;
  /** Give up after this long, in ms, and return the last known status. Default 120000 (2 min). */
  timeoutMs?: number;
  /** Called with each intermediate status while polling. */
  onUpdate?: (payment: Payment) => void;
}

/**
 * Polls GET /payments/{id}/status/ until the payment settles or the timeout
 * elapses. Use it right after a customer returns from a gateway.
 *
 * TWO THINGS NOT TO DO WITH THE RESULT.
 *
 * Do not read the gateway's own query parameters instead. They arrive through the
 * customer's browser, so anybody can type `?success=true`.
 *
 * Do not treat a timeout as a failure. The webhook may still be in flight, an
 * Egyptian wallet confirmation can take minutes, and an InstaPay transfer is
 * confirmed by a person during working hours. Show "we are confirming your
 * payment" and let the order page be the answer — a false "failed" makes
 * customers pay twice.
 *
 * This endpoint has its own throttle scope (`payment_status`, 60/min) because each
 * call can reach a provider, so back the interval off if you poll for long.
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
