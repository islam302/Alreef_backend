# Quickstart: Next.js → login → browse → cart → checkout → COD order

This walks through wiring a brand-new Next.js/TypeScript app up to the real Alreef API,
ending with a placed Cash-on-Delivery order you can see in `GET /api/v1/orders/`. Every
request body below uses the actual field names from the backend serializers (see
[`README.md`](./README.md) and [`types.ts`](./types.ts) for the full reference). For a
ready-made client instead of copy-pasting fetch calls, see
[`../../frontend-sdk/`](../../frontend-sdk/), which implements this exact flow.

Assumes the API is running at `http://localhost:8000` and your Next.js app runs at
`http://localhost:3000` (already the default `CORS_ALLOWED_ORIGINS` /
`CSRF_TRUSTED_ORIGINS` in `config/settings/base.py`).

```bash
npx create-next-app@latest alreef-storefront --typescript
cd alreef-storefront
npm install axios
```

Set `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1` in `.env.local`.

## 1. A minimal API client (axios, with auth + 401 refresh)

```ts
// lib/api-client.ts
import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from "axios";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// Swap this for a cookie-backed store later without touching the rest of the file.
const tokenStore = {
  access: null as string | null,
  refresh: typeof window !== "undefined" ? localStorage.getItem("refresh_token") : null,
  setAccess(token: string | null) {
    this.access = token;
  },
  setRefresh(token: string | null) {
    this.refresh = token;
    if (typeof window !== "undefined") {
      if (token) localStorage.setItem("refresh_token", token);
      else localStorage.removeItem("refresh_token");
    }
  },
};

export const api: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  withCredentials: true, // required: guest cart relies on the Django session cookie
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (tokenStore.access) {
    config.headers.Authorization = `Bearer ${tokenStore.access}`;
  }
  return config;
});

let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (!tokenStore.refresh) return null;
  if (!refreshPromise) {
    refreshPromise = axios
      .post(`${BASE_URL}/auth/token/refresh/`, { refresh: tokenStore.refresh })
      .then((res) => {
        const { access, refresh } = res.data.data; // unwrap envelope
        tokenStore.setAccess(access);
        tokenStore.setRefresh(refresh); // rotation is on — always persist the new one
        return access as string;
      })
      .catch(() => {
        tokenStore.setAccess(null);
        tokenStore.setRefresh(null);
        return null;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<any>) => {
    const original = error.config as InternalAxiosRequestConfig & { _retried?: boolean };
    // Refresh on the 401 *status*, not on errors.code: an expired access token comes
    // back as code "VALIDATION_ERROR". See docs/frontend/ERROR-HANDLING.md.
    if (
      error.response?.status === 401 &&
      !original._retried &&
      !original.url?.includes("/auth/token/refresh/")
    ) {
      original._retried = true;
      const newAccess = await refreshAccessToken();
      if (newAccess) {
        original.headers.Authorization = `Bearer ${newAccess}`;
        return api(original);
      }
      // refresh failed — redirect to login in a real app
    }
    return Promise.reject(error);
  }
);

export { tokenStore };
```

## 2. Register + login

```ts
// A one-off registration (skip straight to login if the user already has an account).
await api.post("/auth/register/", {
  email: "amr@example.com",
  first_name: "Amr",
  last_name: "Ali",
  password: "S0me-Strong-Password!",
});
// -> 201, data: User. Does NOT log the user in — login separately.

const loginRes = await api.post("/auth/login/", {
  email: "amr@example.com",
  password: "S0me-Strong-Password!",
});
const { access, refresh, user } = loginRes.data.data;
tokenStore.setAccess(access);
tokenStore.setRefresh(refresh);
```

If you built a guest cart before logging in (see step 3), merge it right after login:

```ts
await api.post("/cart/merge/", { guest_cart_id: guestCartId /* may be undefined */ });
```

## 3. Browse products

```ts
// List + filter
const list = await api.get("/products/", {
  params: { category: "honey", ordering: "-created_at", page: 1 },
});
const products = list.data.data.results; // ProductListItem[]

// Product detail (also records "recently viewed" server-side)
const detail = await api.get(`/products/${products[0].slug}/`);
const product = detail.data.data; // ProductDetail, with variants[]

// Pick the default (or first active) variant to add to cart
const variant = product.variants.find((v: any) => v.is_default) ?? product.variants[0];
```

## 4. Add to cart (works while still logged out)

```ts
const cartRes = await api.post("/cart/items/", { variant_id: variant.id, quantity: 2 });
const cart = cartRes.data.data; // Cart
const guestCartId = cart.id; // stash this (UUID) if the user isn't logged in yet
```

`GET /cart/` any time to re-fetch the current cart; `PATCH /cart/items/{id}/` with
`{ "quantity": 0 }` removes an item.

## 5. Get/create a shipping address

```ts
// Governorates are needed to create an address (governorate is an id, not a code)
const govs = await api.get("/shipping/governorates/");
const cairo = govs.data.data.find((g: any) => g.code === "cairo");

const addressRes = await api.post("/addresses/", {
  label: "home",
  full_name: "Amr Ali",
  phone: "+201234567890",
  governorate: cairo.id,
  city: null,
  district: "Nasr City",
  street: "10 Makram Ebeid St.",
  building: "5",
  floor: "3",
  apartment: "12",
  landmark: "",
  postal_code: "",
  notes: "",
  is_default: true,
});
const address = addressRes.data.data; // Address
```

## 6. Get a shipping quote and pick a method

```ts
const quotesRes = await api.get("/shipping/quotes/", {
  params: { governorate: cairo.code, subtotal: cart.subtotal, currency: cart.currency },
});
const quotes = quotesRes.data.data; // ShippingQuote[]
const shippingMethod = quotes[0]; // e.g. cheapest/standard
```

## 7. Preview totals, then place a Cash-on-Delivery order

```ts
const checkoutInput = {
  address_id: address.id,
  shipping_method_id: shippingMethod.method_id,
  payment_method: "cod",
  coupon_code: "",
  notes: "",
};

// 7a. Preview — call this again any time address/shipping/coupon changes.
const summary = await api.post("/checkout/summary/", checkoutInput);
console.log(summary.data.data.grand_total); // authoritative total, e.g. "925.00"

// 7b. Place the order — REQUIRES Idempotency-Key. Generate once per checkout attempt
//     and reuse it if you need to retry after a network error.
const idempotencyKey = crypto.randomUUID();
const orderRes = await api.post("/checkout/", checkoutInput, {
  headers: { "Idempotency-Key": idempotencyKey },
});
const { order, payment } = orderRes.data.data; // CheckoutResult
```

For `payment_method: "cod"`, the backend immediately marks the payment `captured`
server-side (no gateway round-trip) — `order.status` will already reflect that by the
time this call returns. For any other `payment_method`, you'd instead redirect the
user to `payment.redirect_url` (or embed `payment.iframe_url`) and then poll
`GET /payments/{payment.id}/status/` until it settles — see `README.md` → Payments.

## 8. View the order

```ts
const orderNumber = order.order_number;
const myOrders = await api.get("/orders/"); // paginated OrderListItem[]
const orderDetail = await api.get(`/orders/${orderNumber}/`); // OrderDetail
```

That's the full happy path. From here:
- `POST /orders/{order_number}/cancel/` with `{ reason }` while status still allows it.
- `POST /orders/{order_number}/returns/` once delivered.
- `GET /shipping/track/{tracking_number}/` once a shipment exists.

See `README.md` for every other domain (wishlist, reviews, loyalty, gift cards,
subscriptions, notifications) and the full error-code table.
