/**
 * Order endpoints — apps/orders/urls.py. All IsAuthenticated, scoped to the
 * requesting user. Detail/cancel/returns are looked up by `order_number`
 * (e.g. "ORD-20260814-0001"), not the numeric `id`.
 */
import { apiGet, apiPost } from "./api-client";
import type {
  OrderDetail,
  OrderListItem,
  PaginatedResponse,
  ReturnRequest,
  ReturnRequestInput,
} from "./types";

/** GET /orders/ — paginated. */
export async function listOrders(params?: { page?: number; page_size?: number }): Promise<PaginatedResponse<OrderListItem>> {
  return apiGet<PaginatedResponse<OrderListItem>>("/orders/", { params });
}

/** GET /orders/{order_number}/ */
export async function getOrder(orderNumber: string): Promise<OrderDetail> {
  return apiGet<OrderDetail>(`/orders/${orderNumber}/`);
}

/**
 * POST /orders/{order_number}/cancel/ — fails with 409 ORDER_CANNOT_CANCEL
 * once the order is shipped or further along (see
 * apps/orders/models.py::ORDER_STATUS_TRANSITIONS).
 */
export async function cancelOrder(orderNumber: string, reason: string): Promise<OrderDetail> {
  return apiPost<OrderDetail>(`/orders/${orderNumber}/cancel/`, { reason });
}

/** POST /orders/{order_number}/returns/ */
export async function createReturnRequest(
  orderNumber: string,
  payload: ReturnRequestInput
): Promise<ReturnRequest> {
  return apiPost<ReturnRequest>(`/orders/${orderNumber}/returns/`, payload);
}

/** GET /orders/returns/ — plain array across all of the user's orders. */
export async function listReturns(): Promise<ReturnRequest[]> {
  return apiGet<ReturnRequest[]>("/orders/returns/");
}
