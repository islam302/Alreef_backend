/**
 * Cart endpoints — apps/carts/urls.py. Works for both guest (session cookie)
 * and authenticated (JWT) users; api-client.ts sets withCredentials so the
 * guest session cookie always round-trips. See docs/frontend/README.md ->
 * "Guest sessions & cookies" and "Cart" for the merge-on-login flow.
 */
import { apiDelete, apiGet, apiPatch, apiPost } from "./api-client";
import type { Cart } from "./types";

/** GET /cart/ */
export async function getCart(): Promise<Cart> {
  return apiGet<Cart>("/cart/");
}

/** DELETE /cart/ — clears all items and the applied coupon. */
export async function clearCart(): Promise<Cart> {
  return apiDelete<Cart>("/cart/");
}

/**
 * POST /cart/items/ — adding a variant already in the cart increments its
 * quantity rather than erroring.
 */
export async function addItem(variantId: number, quantity = 1): Promise<Cart> {
  return apiPost<Cart>("/cart/items/", { variant_id: variantId, quantity });
}

/**
 * PATCH /cart/items/{item_id}/ — quantity: 0 removes the item (not an
 * error), matching the backend's behavior in apps/carts/services.py.
 */
export async function updateItem(itemId: number, quantity: number): Promise<Cart> {
  return apiPatch<Cart>(`/cart/items/${itemId}/`, { quantity });
}

/** DELETE /cart/items/{item_id}/ */
export async function removeItem(itemId: number): Promise<Cart> {
  return apiDelete<Cart>(`/cart/items/${itemId}/`);
}

/** POST /cart/coupon/ */
export async function applyCoupon(code: string): Promise<Cart> {
  return apiPost<Cart>("/cart/coupon/", { code });
}

/** DELETE /cart/coupon/ */
export async function removeCoupon(): Promise<Cart> {
  return apiDelete<Cart>("/cart/coupon/");
}

/**
 * POST /cart/merge/ — call right after login. `guestCartId` is the `id`
 * (UUID) of the cart you had while browsing anonymously, if any. Safe to
 * call with no id (or omitted) as a post-login "sync" no-op.
 */
export async function mergeGuestCart(guestCartId?: string): Promise<Cart> {
  return apiPost<Cart>("/cart/merge/", guestCartId ? { guest_cart_id: guestCartId } : {});
}
