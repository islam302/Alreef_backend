# Database Architecture

PostgreSQL is the only supported engine (`config/settings/base.py`:
`ENGINE = "django.db.backends.postgresql"`). Connection pooling is via
`CONN_MAX_AGE` (60s in `base.py`, raised to 600s in
`config/settings/production.py`) — persistent connections reused across
requests within a worker process rather than a full pgbouncer-style pool.

Almost every model inherits `apps.core.models.BaseModel` (→
`TimeStampedModel`: `created_at`/`updated_at`, `BigAutoField` pk). `Cart` is
the one model that instead inherits `UUIDPrimaryKeyModel` + `TimeStampedModel`
directly, giving it a non-enumerable UUID primary key (`apps/carts/models.py`).

## Entity-relationship diagram

```mermaid
erDiagram
    USER ||--o{ ADDRESS : has
    USER ||--o{ ORDER : places
    USER ||--o| CUSTOMER_PROFILE : has
    USER ||--o{ CART : owns
    USER ||--o| LOYALTY_ACCOUNT : has
    USER ||--o| STORE_CREDIT_ACCOUNT : has
    USER ||--o{ WISHLIST_ITEM : saves
    USER ||--o{ SUBSCRIPTION : subscribes
    USER ||--o{ REVIEW : writes

    CART ||--o{ CART_ITEM : contains
    CART_ITEM }o--|| PRODUCT_VARIANT : references

    CATEGORY ||--o{ CATEGORY : "parent (MPTT)"
    CATEGORY ||--o{ PRODUCT : classifies
    BRAND ||--o{ PRODUCT : makes

    PRODUCT ||--o{ PRODUCT_VARIANT : has
    PRODUCT ||--o{ PRODUCT_IMAGE : has
    PRODUCT_VARIANT ||--o| INVENTORY_ITEM : "tracked by"
    INVENTORY_ITEM ||--o{ BATCH : "stocked in (FEFO)"
    INVENTORY_ITEM ||--o{ STOCK_RESERVATION : reserves
    INVENTORY_ITEM ||--o{ INVENTORY_MOVEMENT : "audit trail"
    BATCH ||--o{ STOCK_RESERVATION : "allocated from"
    BATCH ||--o{ INVENTORY_MOVEMENT : "audit trail"

    ORDER ||--o{ ORDER_ITEM : contains
    ORDER ||--o{ ORDER_STATUS_HISTORY : logs
    ORDER ||--o{ PAYMENT : "paid via"
    ORDER ||--o{ RETURN_REQUEST : "returned via"
    ORDER ||--o{ SHIPMENT : "shipped via"
    ORDER }o--|| ADDRESS : "snapshot of (JSON, not FK)"
    ORDER_ITEM }o--o| PRODUCT_VARIANT : "snapshot of"
    STOCK_RESERVATION }o--o| ORDER : "consumed by"

    PAYMENT ||--o{ PAYMENT_ATTEMPT : has
    PAYMENT ||--o{ PAYMENT_TRANSACTION : records
    PAYMENT ||--o{ PAYMENT_WEBHOOK_EVENT : receives
    PAYMENT ||--o{ REFUND : refunds

    RETURN_REQUEST ||--o{ RETURN_ITEM : contains
    RETURN_ITEM }o--|| ORDER_ITEM : references

    SHIPPING_ZONE }o--o{ GOVERNORATE : covers
    SHIPPING_ZONE ||--o{ SHIPPING_RATE : "priced by"
    SHIPPING_METHOD ||--o{ SHIPPING_RATE : "priced by"
    GOVERNORATE ||--o{ CITY : contains
    SHIPPING_METHOD ||--o{ SHIPMENT : fulfills

    COUPON ||--o{ COUPON_USAGE : "redeemed via"
    COUPON_USAGE }o--o| ORDER : "applied to"
    PROMOTION }o--o{ PRODUCT : scopes
    PROMOTION }o--o{ CATEGORY : scopes

    GIFT_CARD ||--o{ GIFT_CARD_TRANSACTION : logs
    STORE_CREDIT_ACCOUNT ||--o{ STORE_CREDIT_TRANSACTION : logs
    LOYALTY_ACCOUNT ||--o{ LOYALTY_TRANSACTION : logs

    USER {
        bigint id PK
        string email UK
        string phone UK
        bool email_verified
        bool phone_verified
    }
    ADDRESS {
        bigint id PK
        bigint user_id FK
        fk governorate_id FK
        fk city_id FK
        bool is_default
    }
    PRODUCT {
        bigint id PK
        string slug UK
        string sku UK
        money price
        string tax_category
        string status
        bool published
    }
    PRODUCT_VARIANT {
        bigint id PK
        bigint product_id FK
        string sku UK
        money price
        bool is_active
    }
    INVENTORY_ITEM {
        bigint id PK
        bigint variant_id FK "OneToOne"
        int low_stock_threshold
        bool allow_backorder
    }
    BATCH {
        bigint id PK
        bigint item_id FK
        date expiration_date "nullable, FEFO key"
        int quantity
        bool is_active
    }
    STOCK_RESERVATION {
        bigint id PK
        bigint item_id FK
        bigint batch_id FK "nullable"
        uuid cart_id "nullable"
        bigint order_id FK "nullable"
        string status "active/consumed/released/expired"
        datetime expires_at
    }
    ORDER {
        bigint id PK
        string order_number UK
        bigint user_id FK
        string status
        json shipping_address_snapshot
        money subtotal
        money grand_total
    }
    ORDER_ITEM {
        bigint id PK
        bigint order_id FK
        bigint variant_id FK "nullable, SET_NULL"
        string sku_snapshot
        money unit_price
        money line_total
    }
    PAYMENT {
        bigint id PK
        bigint order_id FK
        string provider
        string status
        string provider_reference
        string idempotency_key
    }
    PAYMENT_WEBHOOK_EVENT {
        bigint id PK
        string provider_event_id UK "replay protection"
        bool processed
    }
    REFUND {
        bigint id PK
        bigint payment_id FK
        bigint order_id FK
        money amount
        string idempotency_key
    }
    RETURN_REQUEST {
        bigint id PK
        bigint order_id FK
        string reason
        string status
    }
    SHIPPING_RATE {
        bigint id PK
        bigint zone_id FK
        bigint method_id FK
        string calculation_type
        money base_price
    }
    COUPON {
        bigint id PK
        string code UK
        string discount_type
        int usage_limit
        int per_user_limit
    }
```

The diagram omits some lower-signal models for readability (e.g.
`CustomerProfile`, `NotificationPreference`, `PaymentAttempt`,
`ShipmentEvent`, `Subscription*`, `LoyaltyTier`) — their FKs follow the same
patterns shown above and are described in their respective app sections
below.

## Key relationships worth calling out

- **Orders are immutable snapshots, not live joins.** `Order` stores
  `customer_name`/`customer_email`/`customer_phone` and a full
  `shipping_address_snapshot` JSON blob rather than an `Address` foreign key
  — a customer editing or deleting an address later must never change a past
  order (`apps/orders/models.py` docstring: "spec §37"). Likewise
  `OrderItem` stores `product_name_snapshot`, `variant_name_snapshot`, and
  `sku_snapshot` alongside nullable `product`/`variant` FKs
  (`on_delete=models.SET_NULL`) — if a product is deleted, the historical
  order line survives with its snapshot data intact.
- **`InventoryItem` is one-to-one with `ProductVariant`**, not `Product` —
  stock is tracked per purchasable SKU. Its `quantity_on_hand` is a computed
  property (`sum` over active `Batch.quantity`), not a stored counter.
- **`StockReservation` bridges `Cart`/`Order`/`InventoryItem`/`Batch`** and is
  the concurrency-safety mechanism described in `docs/architecture/inventory.md`.
  It optionally points at `cart_id` (a bare `UUIDField`, not an FK — carts can
  be deleted/merged without cascading) during checkout, and at `order` once an
  order exists.
- **`Payment` → `PaymentTransaction`/`PaymentWebhookEvent`/`Refund`.** A
  `Payment` is created once per checkout attempt; every provider round-trip
  (authorize/capture/refund) is separately recorded as a `PaymentTransaction`
  for reconciliation, and every inbound webhook as a `PaymentWebhookEvent`.
- **`Category` is a `django-mptt` tree** (`MPTTModel` + `TreeForeignKey` to
  `self`), giving efficient subtree queries (`get_descendants()`,
  `get_ancestors()`) without recursive SQL.
- **Coupons and Promotions both scope via M2M to `Product`/`Category`/`Brand`**
  but are otherwise independent: `Coupon` requires a customer-entered code and
  tracks redemptions in `CouponUsage`; `Promotion` applies automatically
  (`apps/promotions/models.py`: "applies at checkout without a customer-entered
  code") and is prioritized/stackable via `priority`/`stackable` fields.

## Indexes and why

Every model declares its indexes explicitly in `Meta.indexes` — a sample of
the more important ones and their rationale:

| Model | Index | Why |
|---|---|---|
| `Order` | `["order_number"]`, `["user", "status"]`, `["status"]`, `["created_at"]` | Order lookup by number (customer-facing), "my orders" filtered by status, admin dashboards filtering/sorting by status and recency |
| `OrderItem` | `["order"]`, `["product"]` | Line-item lookup per order; product sales history |
| `Payment` | `["order", "status"]`, `["provider_reference"]`, `["idempotency_key"]` | Find a payment awaiting confirmation for an order; look up by gateway reference on webhook; dedupe by idempotency key |
| `PaymentWebhookEvent` | `provider_event_id` **unique**, `["provider", "processed"]` | Unique constraint is the actual replay-protection mechanism (see `docs/architecture/payments.md`); `processed` filter for reconciliation sweeps |
| `Batch` | `["item", "is_active"]`, `["expiration_date"]`, `unique_together (item, batch_number)` | `is_active` filter is on every FEFO query; `expiration_date` is the FEFO sort key and is scanned by the expiration-alert/deactivation Celery tasks |
| `StockReservation` | `["item", "status"]`, `["cart_id"]`, `["expires_at"]` | Available-quantity calculation filters by `(item, status=ACTIVE)`; TTL sweep (`release_expired_reservations_task`) filters by `expires_at` |
| `InventoryMovement` | `["item", "movement_type"]`, `["reference"]` | Per-item audit history; look up all movements tied to one order number |
| `Product` | `["slug"]`, `["sku"]`, `["status", "published"]`, `["category"]`, `["brand"]` | Slug/SKU are the two public lookup keys; `(status, published)` is the exact filter every storefront listing applies (`ProductStatus.ACTIVE, published=True` in `apps/catalog/views.py`) |
| `ProductVariant` | `["sku"]`, `["product", "is_active"]` | SKU lookup; active variants per product |
| `Cart` | `["user", "status"]`, `["session_key", "status"]`, `["last_activity_at"]` | Guest carts are found by `session_key`, authenticated by `user`, both filtered to `status=OPEN`; `last_activity_at` powers the abandoned-cart sweep |
| `User` | `["email"]`, `["phone"]` | `email` is `USERNAME_FIELD` (login lookup); `phone` for OTP flows |
| `AuditLog` | `["model_name", "object_id"]`, `["actor", "created_at"]`, `["action"]` | "History for this object", "actions by this admin", filter by action type |
| `Coupon` | `["code"]`, `["is_active"]` | Code lookup at checkout; admin listing of active coupons |
| `Notification` | `["user", "read_at"]` | Unread-count / inbox queries |

## Constraint choices

- **`unique_together` over `UniqueConstraint`**: the codebase consistently
  uses the classic `Meta.unique_together` (e.g. `CartItem (cart, variant)`,
  `Batch (item, batch_number)`, `ShippingRate (zone, method)`,
  `WishlistItem (user, product)`, `ReturnItem (return_request, order_item)`,
  `Review (product, user, order_item)`). This enforces "one of X per Y" at
  the database level rather than relying on application-level checks alone.
- **`PaymentWebhookEvent.provider_event_id` is globally unique** (not
  `unique_together`) — this single column is the entire webhook-replay
  defense: `get_or_create(provider_event_id=...)` in
  `apps/payments/services.process_webhook` makes a duplicated/retried webhook
  delivery a guaranteed no-op regardless of provider or timing.
- **`select_for_update()` row locking** is used everywhere two concurrent
  requests could race on a mutable balance or state machine:
  - `apps/inventory/services.py`: `InventoryItem.objects.select_for_update()`
    and `Batch.objects.select_for_update()` inside `reserve_stock`/
    `consume_reservation`/`release_expired_reservations` — this is what
    prevents two customers from both reserving the last unit of stock (see
    `docs/architecture/inventory.md`).
  - `apps/orders/services.py`: `Order.objects.select_for_update()` in
    `transition_status` — serializes concurrent status-transition attempts
    against the same order.
  - `apps/payments/services.py`: `Payment.objects.select_for_update()` in
    `mark_payment_captured`/`mark_payment_failed`/`process_webhook`/
    `create_refund` — prevents a webhook and a manual admin action (or two
    webhook retries) from double-capturing or double-refunding the same
    payment.
  All of the above run inside `@transaction.atomic` blocks, so the lock is
  held for the minimum necessary scope and released at commit.
- **`on_delete` choices encode business intent**: `Order.user` and
  `ReturnRequest.order` use `PROTECT` (a user or order with financial history
  cannot be deleted out from under it); `OrderItem.variant`/`OrderItem.product`
  use `SET_NULL` (catalog deletion must not cascade-delete order history,
  hence the snapshot fields); `CartItem`/`Batch`/`InventoryMovement` use
  `CASCADE` (they are meaningless without their parent).
