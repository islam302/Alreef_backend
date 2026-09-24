# Enums, statuses and state machines

Every fixed-value string the API can send you, plus the rules that decide which buttons
your UI is allowed to show. Values are read from the `TextChoices` classes in
`apps/*/models.py`; transitions from `apps/orders/models.py::ORDER_STATUS_TRANSITIONS`.

Union types for all of these are already in [`types.ts`](./types.ts) — this file explains
what to *do* with them.

---

## Order status

```
pending ──────► confirmed ──► processing ──► packed ──► shipped ──┬──► out_for_delivery ──► delivered
   │                │              │            │                 │                            │
   └────────────────┴──────────────┴────────────┴──► cancelled     └──► return_requested ◄──────┘
                                                                            │
                                                            ┌───────────────┴──────────────┐
                                                            ▼                              ▼
                                                        returned ──► refunded          delivered
                                                            │         (rejected: back to delivered)
                                                            └──► partially_refunded ──► refunded
```

The exact map (this is the server's source of truth — a transition not listed here
returns `409 ORDER_INVALID_TRANSITION`):

| From | Can move to |
|---|---|
| `pending` | `confirmed`, `cancelled` |
| `confirmed` | `processing`, `cancelled` |
| `processing` | `packed`, `cancelled` |
| `packed` | `shipped`, `cancelled` |
| `shipped` | `out_for_delivery`, `delivered`, `return_requested` |
| `out_for_delivery` | `delivered`, `return_requested` |
| `delivered` | `return_requested` |
| `cancelled` | *(terminal)* |
| `return_requested` | `returned`, `delivered` *(if the return is rejected)* |
| `returned` | `refunded`, `partially_refunded` |
| `refunded` | *(terminal)* |
| `partially_refunded` | `refunded` |

### Which actions to show

The customer can only ever trigger two of these transitions. Derive the buttons, don't
hardcode them per screen:

```ts
const CANCELLABLE: OrderStatus[] = ["pending", "confirmed", "processing", "packed"];

export const canCancel = (o: OrderDetail) => CANCELLABLE.includes(o.status);

export const canTrack = (o: OrderDetail) =>
  ["shipped", "out_for_delivery", "delivered"].includes(o.status);

export const canReturn = (o: OrderDetail) =>
  o.status === "delivered" && withinReturnWindow(o.delivered_at);

export const canReview = (o: OrderDetail) => o.status === "delivered";
```

- **Cancel** (`POST /orders/{order_number}/cancel/`) — allowed up to and including
  `packed`. Once `shipped`, the server refuses with `409 ORDER_CANNOT_CANCEL`. Hide the
  button rather than letting the user discover this.
- **Return** (`POST /orders/{order_number}/returns/`) — the order must be exactly
  `delivered`, and `delivered_at` must be within the return window. **The window is not
  exposed by the API**: it lives in `ReturnPolicySettings` (default **14 days**) and is
  admin-editable, so a hardcoded frontend check can drift. Show the button on any
  `delivered` order and handle a `409 ORDER_INVALID_TRANSITION` gracefully, or agree a
  fixed number with the backend team and keep it in one constant.
- Change-of-mind returns can be disabled server-side
  (`allow_change_of_mind_returns`), which also isn't exposed. The same 409 tells you.

A successful return request flips the order itself to `return_requested`, so refetch the
order after submitting one — the cancel and return buttons both need to disappear.

### Suggested labels

| Status | Customer-facing (en) | Customer-facing (ar) | Tone |
|---|---|---|---|
| `pending` | Awaiting payment | في انتظار الدفع | neutral |
| `confirmed` | Confirmed | تم التأكيد | positive |
| `processing` | Being prepared | جاري التجهيز | progress |
| `packed` | Packed | تم التغليف | progress |
| `shipped` | Shipped | تم الشحن | progress |
| `out_for_delivery` | Out for delivery | خرج للتوصيل | progress |
| `delivered` | Delivered | تم التسليم | success |
| `cancelled` | Cancelled | ملغي | muted |
| `return_requested` | Return requested | تم طلب الإرجاع | warning |
| `returned` | Returned | تم الإرجاع | neutral |
| `refunded` | Refunded | تم رد المبلغ | success |
| `partially_refunded` | Partially refunded | رد جزئي للمبلغ | neutral |

Arabic here is unreviewed — have it checked before launch.

### Building a progress tracker

`OrderDetail.status_history` gives you `{ from_status, to_status, note, created_at }`
entries in order, which is what you want for a vertical timeline. The dedicated
timestamps (`confirmed_at`, `shipped_at`, `delivered_at`, `cancelled_at`) are only set
for those four states; everything else has to come from the history.

Don't render a fixed 5-step bar — an order can jump `shipped → delivered` with no
`out_for_delivery`, and cancelled/returned orders leave the happy path entirely.

---

## Payment status

| Value | Meaning | Terminal? |
|---|---|---|
| `pending` | Created, not paid. COD orders sit here until the courier collects. | No |
| `authorized` | Funds held, not captured. | No |
| `captured` | **Paid.** The only status that means money arrived. | Yes* |
| `failed` | The gateway declined. | Yes |
| `cancelled` | Abandoned or voided before capture. | Yes |
| `expired` | The payment window closed unpaid. | Yes |
| `refunded` | Fully refunded. | Yes |
| `partially_refunded` | Some of it refunded. | No |

\* `captured` can still move to `refunded` / `partially_refunded` later.

Stop polling on `captured`, `failed`, `cancelled`, `expired`. See
[`PAYMENTS.md`](./PAYMENTS.md).

**Do not infer paid-ness from the order status.** `confirmed` means the payment webhook
already landed, but the payment record is the authority, and for COD `captured` means
"cash collected", not "card charged".

## Payment method vs payment provider

Two different enums, easy to confuse.

**`payment_method`** — what the user picks. This is what goes in `CheckoutInput` and
what you see on `OrderDetail.payment_method`:

`card` · `mobile_wallet` · `instapay` · `cod`

**`provider`** — which gateway the backend routed it to. You see this on the `Payment`
object:

`paymob` · `instapay` · `cod` · `manual`

The mapping (`apps/payments/config.gateway_for_method`):

| Method | Provider |
|---|---|
| `card` | `paymob` |
| `mobile_wallet` | `paymob` |
| `instapay` | `instapay`, or `paymob` once Paymob enables it |
| `cod` | `cod` |

`instapay` is the one method whose provider can change without its code changing:
InstaPay publishes no merchant API today, so the customer transfers from their banking
app and a staff member confirms. The moment `PAYMOB_INTEGRATION_ID_INSTAPAY` is set, the
same method routes through Paymob instead. That is exactly why you should label your UI
with the **method**, not the provider — the method is the promise you made the customer,
the provider is an implementation detail that moves.

## Shipment status

Tracking (`GET /shipping/track/{tracking_number}/`) uses its own enum, close to but not
the same as order status:

`pending` · `picked_up` · `in_transit` · `out_for_delivery` · `delivered` · `failed` ·
`returned` · `cancelled`

`failed` means a delivery attempt failed (nobody home, wrong address) — the shipment is
usually retried, so don't present it as final. Each `ShipmentEvent` carries `status`,
`description`, `location` and `occurred_at`; render the events, not just the top-level
status.

## Return request status

`requested` → `approved` | `rejected` → `item_received` → `refunded` → `closed`

Whether a new request starts at `requested` or jumps straight to `approved` depends on
the admin's `require_approval` setting, so handle both as the initial state. `rejected`
sends the order back to `delivered`, which means the return button reappears — that's
intended.

## Product

**`product_type`** — `simple` · `variable` · `bundle` · `mix_and_match` · `subscription`.
Drives the product page layout: `variable` needs a variant picker, `bundle` should list
`bundle_items`, `subscription` products belong in the subscription flow.

**`tax_category`** — `standard` (14% VAT) · `zero_rated` (basic food) · `exempt`. Display
only; the backend computes tax.

**`packaging_type`** — `jar` · `bottle` · `box` · `bag` · `can` · `pouch` · `gift_box`.

### Badges — use `computed_badges`, not `badges`

`badges` holds only what an admin stored: `NEW` · `BEST_SELLER` · `SALE` · `LIMITED` ·
`ORGANIC` · `FEATURED`.

`computed_badges` is that list plus live inventory state, recomputed per request:

- `OUT_OF_STOCK` — every active variant is out of stock.
- `LOW_STOCK` — at least one variant is below its threshold (and not all are out).
- `SALE` — `compare_at_price > price`, appended even if an admin didn't set the badge.

So `SALE` can appear twice if an admin also set it manually. Dedupe before rendering:

```ts
const badges = [...new Set(product.computed_badges)];
```

### `in_stock` has an edge case

`in_stock` is `true` when a product has **no active variants at all**, because the
serializer returns early before checking inventory. A `simple` product with no variant
rows will therefore look purchasable but has nothing to add to the cart — there is no
`variant_id` to post. Guard the add-to-cart button on a variant actually existing:

```ts
const purchasable = product.in_stock && product.variants.length > 0;
```

## Cart status

`open` · `merged` · `converted` · `abandoned`. You will effectively only ever see `open`
— `get_or_create_cart` returns an open cart, and the others are terminal bookkeeping
states. If you ever cache a cart id and it comes back `converted`, the order was already
placed: fetch a fresh cart.

## Coupons and promotions

**`CouponDiscountType`** — `percentage` · `fixed_amount` · `free_shipping`. Use this from
`GET /coupons/{code}/` to render the terms before the user applies it.

**`PromotionType`** — `percentage` · `fixed_amount` · `free_shipping` · `buy_x_get_y` ·
`bundle_discount`. Promotions are automatic: the backend picks and applies them at
checkout. `GET /promotions/` is for a "current deals" banner only; there is nothing for
the user to click.

## Shipping

**`ShippingMethodType`** — `standard` · `express` · `same_day` · `pickup`. `pickup` means
no delivery address is needed in a strict sense, but `CheckoutInput` still requires an
`address_id`, so keep the address step.

**`RateCalculationType`** — `flat` · `weight_based` · `price_based` · `free`. Internal;
you receive a computed `price` on each quote.

## Subscriptions

**`SubscriptionFrequency`** — `weekly` · `monthly` · `quarterly` · `custom`. `custom`
requires `custom_interval_days` in the create body; omitting it is a validation error.

**`SubscriptionStatus`** — `active` · `paused` · `cancelled`. Show pause on `active`,
resume on `paused`, nothing on `cancelled` (terminal — the user creates a new
subscription instead).

## Users and addresses

**`Gender`** — `male` · `female` · `unspecified`.
**`Language`** — `ar` · `en` (on `profile.language`).
**`AddressLabel`** — `home` · `work` · `other`.

## Notifications

**`NotificationEvent`** — `registration` · `email_verification` · `phone_verification` ·
`password_reset` · `order_created` · `payment_success` · `payment_failed` ·
`order_shipped` · `out_for_delivery` · `order_delivered` · `refund_processed` ·
`low_stock` · `abandoned_cart` · `promotion` · `expiration_alert`.

Use the event to pick an icon and a deep link — `order_*` and `payment_*` events should
route to the order page.

**`NotificationChannel`** — `email` · `sms` · `whatsapp` · `push` · `in_app`. The bell
feed only shows `in_app`. The preference flags (`marketing_email`, `marketing_sms`,
`marketing_whatsapp`, `marketing_push`) gate **marketing** only — transactional
notifications ignore them, so don't present the toggles as "turn off all emails".

## Loyalty and gift cards

**`LoyaltyTransactionType`** — `earn` · `redeem` · `expire` · `adjust`. `expire` and
negative `adjust` entries reduce the balance; colour them accordingly in the history
list.

**`GiftCardStatus`** — `active` · `redeemed` · `expired` · `disabled`.

**`StoreCreditReason`** — `refund` · `promotional` · `manual_admin` · `redemption`.

## Attributes

**`AttributeInputType`** — `select` · `multiselect` · `text` · `number` · `boolean`.
Drives your filter sidebar widget: `select` → radio list, `multiselect` → checkboxes,
`number` → range input, `boolean` → toggle. Only attributes with `is_filterable: true`
come back from `GET /attributes/`, and `is_variant_attribute: true` marks the ones that
define variants (size, weight) rather than merely describing the product.

## Analytics events

The `event_type` values accepted by `POST /admin-api/events/track/`:

`product_view` · `search` · `add_to_cart` · `remove_from_cart` · `checkout_started` ·
`payment_started` · `payment_success` · `payment_failed` · `order_created` ·
`order_delivered` · `refund_created` · `coupon_used` · `review_created`

Anything else is a `400 VALIDATION_ERROR`.
