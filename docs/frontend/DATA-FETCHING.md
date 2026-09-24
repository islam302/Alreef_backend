# Data fetching patterns

Opinionated guidance for wiring this API into a React/Next.js storefront: where to put
the envelope unwrapping, what to render on the server, how to key your cache, and which
endpoints have behaviour that breaks the obvious approach.

Examples use TanStack Query and the Next.js App Router because that's the common pairing
— the reasoning transfers to SWR, Redux Toolkit Query or plain hooks.

## Unwrap the envelope once, at the transport layer

Every response is `{ success, message, data, errors }`. Nothing above the HTTP client
should know that.

```ts
// lib/api.ts
const client = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL,
  withCredentials: true, // guest cart rides on the Django session cookie
});

export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.get<ApiResponse<T>>(url, config);
  return res.data.data as T;
}

export async function post<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await client.post<ApiResponse<T>>(url, body, config);
  return res.data.data as T;
}
```

Two shapes come back under `data`, and which one an endpoint uses is a fixed property of
that endpoint, not something to sniff at runtime — [`ENDPOINTS.md`](./ENDPOINTS.md) lists
every one. Encode it in the call signature:

```ts
export const getProducts = (params: ProductQuery) =>
  get<PaginatedResponse<ProductListItem>>("/products/", { params });

export const getCategories = () => get<Category[]>("/categories/"); // plain array
```

Getting this wrong is the single most common integration bug here, because `categories`,
`attributes`, `promotions`, `wishlist`, `subscriptions`, `addresses`, `governorates`,
`cities`, `quotes` and `related` are arrays while their neighbours are pages.

The `message` field is worth surfacing for mutations — `"Coupon applied."`,
`"Order cancelled."` are already written for the user. Return it alongside the data if
you want to drive toasts from the server's wording:

```ts
export async function postWithMessage<T>(url: string, body?: unknown) {
  const res = await client.post<ApiResponse<T>>(url, body);
  return { data: res.data.data as T, message: res.data.message };
}
```

## Query keys

Hierarchical keys make invalidation obvious. One factory, exported from one file:

```ts
export const keys = {
  products: {
    all: ["products"] as const,
    list: (q: ProductQuery) => ["products", "list", q] as const,
    detail: (slug: string) => ["products", "detail", slug] as const,
    related: (slug: string) => ["products", "related", slug] as const,
    reviews: (id: number) => ["products", "reviews", id] as const,
  },
  cart: ["cart"] as const,
  wishlist: ["wishlist"] as const,
  addresses: ["addresses"] as const,
  orders: {
    all: ["orders"] as const,
    list: (page: number) => ["orders", "list", page] as const,
    detail: (orderNumber: string) => ["orders", "detail", orderNumber] as const,
  },
  me: ["me"] as const,
  checkoutSummary: (input: CheckoutInput) => ["checkout", "summary", input] as const,
};
```

Note `orders.detail` keys on `order_number`, matching the API's lookup field. Keying on
the numeric `id` means you can't build the key from a route param.

## Cache times that match the data

| Data | `staleTime` | Why |
|---|---|---|
| Categories, brands, attributes, governorates, cities | 1 hour+ | Reference data. Effectively static within a session. |
| Product list / search | 1-5 min | Prices and stock move, but not per second. |
| Product detail | 1-5 min | Same, but see the prefetch warning below. |
| Cart | 0 | Always fresh. It's the number the user is about to pay. |
| Checkout summary | 0 | Authoritative money. Never serve a cached total. |
| Orders list / detail | 30s | Status changes are driven by staff, not the user. |
| Payment status | 0 | Poll it; see [`PAYMENTS.md`](./PAYMENTS.md). |
| `me`, addresses, wishlist | 5 min | Changes only through the user's own actions, which you invalidate on. |

## Don't prefetch product detail on hover

`GET /products/{slug}/` records a **recently-viewed** entry for the current user or
session as a side effect. Prefetching on hover therefore fills the user's
recently-viewed list with things they only glanced at, and
`GET /products/recently-viewed/` becomes noise.

Build hover cards from the list payload you already have — `ProductListItem` carries
name, price, `primary_image`, `computed_badges` and `in_stock`, which is everything a
card needs. If you genuinely need detail-level data early, accept the side effect
knowingly or ask the backend for a prefetch-safe flag.

## Cart: every response is the whole cart

`/cart/`, `/cart/items/`, `/cart/coupon/` and `/cart/merge/` all return the complete
`Cart`, on every verb including `DELETE`. So never invalidate-and-refetch after a cart
mutation — just write the response into the cache:

```ts
export function useAddToCart() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { variant_id: number; quantity?: number }) =>
      post<Cart>("/cart/items/", input),
    onSuccess: (cart) => qc.setQueryData(keys.cart, cart), // one round trip, not two
  });
}
```

Optimistic updates are worth it for the quantity stepper, where the latency is visible:

```ts
export function useUpdateCartItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, quantity }: { itemId: number; quantity: number }) =>
      client.patch<ApiResponse<Cart>>(`/cart/items/${itemId}/`, { quantity }).then((r) => r.data.data),
    onMutate: async ({ itemId, quantity }) => {
      await qc.cancelQueries({ queryKey: keys.cart });
      const previous = qc.getQueryData<Cart>(keys.cart);
      qc.setQueryData<Cart>(keys.cart, (old) =>
        old && {
          ...old,
          // quantity 0 deletes the line server-side — mirror that locally
          items: quantity === 0
            ? old.items.filter((i) => i.id !== itemId)
            : old.items.map((i) => (i.id === itemId ? { ...i, quantity } : i)),
        }
      );
      return { previous };
    },
    onError: (_e, _v, ctx) => ctx?.previous && qc.setQueryData(keys.cart, ctx.previous),
    onSuccess: (cart) => qc.setQueryData(keys.cart, cart),
  });
}
```

Two server behaviours to mirror optimistically or you'll get a visible snap-back:
`quantity: 0` on PATCH **deletes** the line, and posting a `variant_id` that's already
in the cart **increments** rather than adding a second line.

Don't try to recompute `subtotal` optimistically. Prices are snapshotted server-side at
add time (`unit_price_snapshot`) and refreshed on quantity updates; let the response
carry the number.

## Login has to merge the guest cart

After any successful login or register+login, call `POST /cart/merge/` with the guest
cart id before you let the user go anywhere:

```ts
export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (creds: { email: string; password: string }) => {
      const guestCartId = qc.getQueryData<Cart>(keys.cart)?.id;
      const result = await post<LoginResult>("/auth/login/", creds);
      tokenStore.set(result.access, result.refresh);
      const cart = await post<Cart>("/cart/merge/", guestCartId ? { guest_cart_id: guestCartId } : {});
      return { result, cart };
    },
    onSuccess: ({ result, cart }) => {
      qc.setQueryData(keys.me, result.user);
      qc.setQueryData(keys.cart, cart);
      qc.invalidateQueries({ queryKey: keys.wishlist });
      qc.invalidateQueries({ queryKey: keys.orders.all });
    },
  });
}
```

Calling merge with no `guest_cart_id` just returns the user's current cart, so it is safe
to call unconditionally — treat it as a post-login cart sync rather than a special case.
Grab the guest cart id *before* logging in; the cache is the easiest place to find it.

On logout, clear everything user-scoped: `qc.removeQueries()` for cart, wishlist, orders,
addresses, me. Leaving a stale cart in the cache after logout leaks one user's basket
into the next session on a shared device.

## Pagination

The page shape gives you `next` and `previous` as **full URLs**, plus `num_pages` and
`current_page`. For numbered pagination use the page numbers; for infinite scroll, use
`next` as the cursor:

```ts
useInfiniteQuery({
  queryKey: keys.products.list(filters),
  queryFn: ({ pageParam = 1 }) => getProducts({ ...filters, page: pageParam }),
  getNextPageParam: (last) => (last.next ? last.current_page + 1 : undefined),
  initialPageParam: 1,
});
```

`page_size` caps at **100**; asking for more silently gives you 100. Don't try to fetch a
whole catalogue in one request.

Paginated list endpoints under the `search` throttle scope (products, search) allow
60/min. Infinite scroll with an aggressive intersection observer can trip that — debounce
the trigger.

## Server-side rendering

What's safe to render on the server, and what isn't, comes down to one question: does the
endpoint need the user's credentials?

**Render on the server** — anything public and cacheable: product lists, product detail,
categories, brands, search results, promotions. These are also the pages that need SEO,
which makes the split convenient.

```tsx
// app/products/[slug]/page.tsx
export default async function ProductPage({ params }: { params: { slug: string } }) {
  const res = await fetch(`${process.env.API_BASE_URL}/products/${params.slug}/`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) notFound();
  const { data: product } = (await res.json()) as ApiResponse<ProductDetail>;
  return <ProductDetail product={product} />;
}
```

Use a server-only `API_BASE_URL` (container-internal hostname in Docker) distinct from
the browser's `NEXT_PUBLIC_API_BASE_URL`.

**Render on the client** — the cart, and anything else session- or JWT-scoped. The guest
cart is keyed to the Django session cookie; to fetch it server-side you'd have to forward
the incoming `sessionid` cookie *and* relay any `Set-Cookie` back to the browser so a new
anonymous session isn't minted per render. That's fragile and easy to get subtly wrong.
Render a cart skeleton and fetch on the client.

The same applies to JWT-authenticated data if you store the access token in memory: the
server has no way to read it. If you want authenticated SSR, put the tokens in httpOnly
cookies via a Next route handler acting as a backend-for-frontend, then forward them
deliberately. That's a real architectural choice, not a detail — decide it before you
build the auth layer, not after.

## Keeping the checkout total honest

The checkout summary must be server-computed on every change to address, shipping method,
coupon **or payment method** (the COD fee moves the total).

```ts
const { data: totals, isFetching } = useQuery({
  queryKey: keys.checkoutSummary(input),
  queryFn: () => post<CheckoutTotals>("/checkout/summary/", input),
  enabled: Boolean(input.address_id && input.shipping_method_id),
  staleTime: 0,
  placeholderData: keepPreviousData, // avoid a flash of empty totals while refetching
});
```

Disable the pay button while `isFetching` is true. Letting someone click *Pay 542.48*
while a new total is in flight is how you get a support ticket about a charge that didn't
match the screen.

Never derive the total client-side, not even "just for the optimistic render". VAT is
per-product (`tax_category`), promotions stack by server rules, and shipping is
zone-based. Any local approximation will disagree with the server eventually.

## Mutations and what to invalidate

| Mutation | Do this |
|---|---|
| Add / update / remove cart item | `setQueryData(keys.cart, response)` |
| Apply / remove coupon | `setQueryData(keys.cart, response)`, invalidate checkout summary |
| Create / update / delete address | invalidate `keys.addresses`, invalidate checkout summary |
| `POST /checkout/` | invalidate `keys.cart` (server marked it converted), invalidate `keys.orders.all`, seed `keys.orders.detail(order_number)` from the response |
| Cancel order | `setQueryData(keys.orders.detail(n), response)`, invalidate `keys.orders.all` |
| Create return | invalidate the order detail — the order flipped to `return_requested` |
| Wishlist add/remove | invalidate `keys.wishlist` and the product's `check` query |
| `PATCH /auth/me/` | `setQueryData(keys.me, response)` |
| Create review | don't optimistically add it — reviews are moderated and won't appear until approved. Show "submitted for review" instead. |

## Retries

Configure the default so 4xx never retries — most of this API's failures are business
rules, and retrying a `COUPON_EXPIRED` three times just slows the error down:

```ts
new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        const status = (error as AxiosError)?.response?.status ?? 0;
        if (status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
      refetchOnWindowFocus: false, // this API throttles; focus-refetch storms trip 429
    },
    mutations: { retry: false }, // see ERROR-HANDLING.md for the checkout exception
  },
});
```

`POST /checkout/` is the one mutation worth retrying, and only because of its
`Idempotency-Key`. Handle it explicitly in that mutation rather than globally.

## Tracking events

`POST /admin-api/events/track/` is public, returns `202`, and processes asynchronously.
Fire and forget — never await it in a click handler, and never let its failure surface:

```ts
export function track(event_type: AnalyticsEventType, payload: Record<string, unknown> = {}) {
  void client.post("/admin-api/events/track/", { event_type, ...payload }).catch(() => {});
}
```

## Related

- [`ENDPOINTS.md`](./ENDPOINTS.md) — array vs page for every endpoint.
- [`ERROR-HANDLING.md`](./ERROR-HANDLING.md) — the 401 interceptor and retry policy.
- [`QUICKSTART.md`](./QUICKSTART.md) — a working client, end to end.
