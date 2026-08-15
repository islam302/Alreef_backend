# Inventory

Files: `apps/inventory/models.py`, `apps/inventory/services.py`,
`apps/inventory/tasks.py`.

## Model shape

- **`InventoryItem`** — one-to-one with `ProductVariant`. Not a stored
  counter; `quantity_on_hand`, `reserved_quantity`, `available_quantity`,
  `is_low_stock`, `is_out_of_stock` are all computed `@property`s derived
  live from `Batch`/`StockReservation` rows:

  ```python
  quantity_on_hand   = sum(batches.filter(is_active=True).quantity)
  reserved_quantity  = sum(reservations.filter(status=ACTIVE).quantity)
  available_quantity = max(quantity_on_hand - reserved_quantity, 0)
  is_low_stock        = 0 < available_quantity <= low_stock_threshold
  is_out_of_stock      = available_quantity <= 0 and not allow_backorder
  ```
- **`Batch`** — a received lot. `expiration_date` is nullable (non-perishable
  goods have no expiry). Default ordering is
  `["expiration_date", "received_at"]` — Postgres NULLs sort last by default
  in ascending order for this ordering clause, but the actual FEFO query
  (below) makes this explicit rather than relying on default `Meta.ordering`.
  `unique_together (item, batch_number)`.
- **`InventoryMovement`** — append-only audit log. Every stock change (in or
  out) is recorded here with a signed `quantity_delta`, a `movement_type`
  (purchase/sale/return/adjustment/damage/expired/transfer/reservation/release),
  and a free-text `reference` (order number, adjustment reason, etc.).
  Docstring: "spec §23 ... every stock change must be traceable."
- **`StockReservation`** — the concurrency-safety primitive. Points at an
  `InventoryItem` and (usually) a specific `Batch`, and at either a `cart_id`
  (bare `UUIDField`, no FK) during browsing/checkout, or an `order` once the
  order is created. Status lifecycle: `ACTIVE → CONSUMED | RELEASED | EXPIRED`.

## FEFO batch allocation (`reserve_stock`)

`apps.inventory.services.reserve_stock(*, variant, quantity, cart_id=None, order=None, ttl_minutes=30)`
is the single entry point for placing a hold on stock. It is called from
`apps.checkout.services.create_order_from_cart` (with `order=` set, no TTL)
and is designed to also be callable earlier in the flow with `cart_id=` set
(TTL-bound) for an "add to cart reserves stock" pattern.

```python
@transaction.atomic
def reserve_stock(*, variant, quantity, cart_id=None, order=None, ttl_minutes=30):
    item = InventoryItem.objects.select_for_update().get(variant=variant)
    ...
    available = item.available_quantity
    if available < quantity and not item.allow_backorder:
        raise ApplicationError(INSUFFICIENT_STOCK, ...)

    batches = (
        Batch.objects.select_for_update()
        .filter(item=item, is_active=True, quantity__gt=0)
        .order_by(F("expiration_date").asc(nulls_last=True), "received_at")
    )

    remaining = quantity
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        StockReservation.objects.create(item=item, batch=batch, quantity=take, ...)
        InventoryMovement.objects.create(..., movement_type=RESERVATION, quantity_delta=-take, ...)
        remaining -= take
    ...
```

**Why `F("expiration_date").asc(nulls_last=True)`**: this orders batches so
the one expiring *soonest* is consumed first (First-Expired-First-Out), while
batches with no expiry (`nulls_last=True`) are only used once every dated
batch is exhausted — the opposite of `nulls_first`, which would consume
non-perishable stock before soon-to-expire stock and let perishables spoil on
the shelf. Within the same expiration date, `received_at` breaks ties
(oldest stock first).

**Why `select_for_update()` on both `InventoryItem` and the `Batch` queryset**:
this is the actual mechanism that prevents the "last item" race condition —
two concurrent requests for the same variant will serialize on the
`InventoryItem` row lock (the first request's transaction holds the lock
until commit/rollback; the second blocks until then and re-reads
`available_quantity` with the first request's changes already visible). Both
locks are acquired inside one `@transaction.atomic` block, so if the
reservation loop cannot fully satisfy `quantity` and backorder is not
allowed, `transaction.set_rollback(True)` discards every partial reservation
already created in that call before raising `INSUFFICIENT_STOCK` — a failed
checkout never leaves a partial hold behind.

A reservation can span multiple batches (e.g. 3 units from a nearly-expired
batch + 7 from the next one) — the function returns a `list[StockReservation]`,
not a single row, precisely to support this.

**Backorder path**: if `allow_backorder=True` on the `InventoryItem` and
batches can't fully cover `quantity`, a final reservation with `batch=None`
covers the shortfall — it isn't tied to any physical lot and is expected to
be fulfilled by a future purchase/restock.

## Reservation lifecycle

| Status | Meaning | Transition trigger |
|---|---|---|
| `ACTIVE` | Stock is held, not yet permanently deducted | Created by `reserve_stock` |
| `CONSUMED` | Permanently deducted from the batch | `consume_reservation()` — called by `confirm_order_after_payment` once payment is confirmed |
| `RELEASED` | Hold freed, stock returns to availability | `release_reservation()` — called on order cancellation (`apps.orders.services.cancel_order`) or TTL expiry sweep |
| `EXPIRED` | *(status value exists but the sweep task currently transitions to `RELEASED`, not `EXPIRED` — see note below)* | — |

```python
@transaction.atomic
def consume_reservation(reservation):
    if reservation.status != ACTIVE:
        return
    if reservation.batch:
        batch = Batch.objects.select_for_update().get(pk=reservation.batch_id)
        if batch.expiration_date and batch.expiration_date <= timezone.now().date():
            raise ApplicationError(NO_VALID_BATCH, "Cannot sell an expired batch.")
        batch.quantity -= reservation.quantity
        batch.save(update_fields=["quantity"])
    reservation.status = CONSUMED
    ...
    InventoryMovement.objects.create(..., movement_type=SALE, quantity_delta=-reservation.quantity, ...)
```

Note the belt-and-suspenders expiry check *at consumption time* too — even
if a batch somehow wasn't yet auto-deactivated by the hourly sweep, selling
an already-expired batch is blocked here as a second line of defense.

`release_reservation()` is the inverse: flips status to `RELEASED` and logs
an `InventoryMovement(RELEASE, quantity_delta=+reservation.quantity)` — the
held quantity becomes available again immediately since `available_quantity`
is computed live from `ACTIVE` reservations only.

### TTL expiry sweep

`reserve_stock` sets `expires_at = now + ttl_minutes` **only when
`cart_id` is set and `order` is not** (`expires_at = ... if (cart_id and not order) else None`)
— i.e., a reservation tied to an in-progress cart/checkout expires in 30
minutes by default if the customer never completes checkout, while a
reservation tied to a confirmed `Order` has no TTL (it's already committed).

`apps.inventory.tasks.release_expired_reservations_task` (Celery Beat, every
5 minutes) calls `release_expired_reservations()`:

```python
@transaction.atomic
def release_expired_reservations():
    expired = StockReservation.objects.select_for_update().filter(
        status=ACTIVE, expires_at__isnull=False, expires_at__lt=timezone.now()
    )
    for reservation in expired:
        release_reservation(reservation)
```

This is what recovers stock held by an abandoned checkout without requiring
any customer action.

## Concurrency safety summary

The combination of (a) locking the `InventoryItem` row before reading
`available_quantity`, (b) locking every candidate `Batch` row before
allocating from it, and (c) doing both inside one atomic transaction, means
two simultaneous requests for the last unit of a variant cannot both
succeed: whichever transaction commits first permanently reduces the
available quantity (via the reservation existing, even before `consume`);
the second transaction, once unblocked, re-evaluates `available_quantity`
against the now-current state and correctly raises `INSUFFICIENT_STOCK` if
nothing is left. Django's default transaction isolation
(`READ COMMITTED` on PostgreSQL) combined with explicit row locks is what
makes this safe — without `select_for_update()`, both transactions could read
the same "1 available" snapshot before either writes.

## Expired-batch auto-deactivation

`apps.inventory.tasks.deactivate_expired_batches_task` (Celery Beat, hourly):

```python
expired = get_expired_batches()  # is_active=True, quantity>0, expiration_date < today
for batch in expired:
    InventoryMovement.objects.create(..., movement_type=EXPIRED, quantity_delta=-batch.quantity, ...)
    batch.is_active = False
    batch.save(update_fields=["is_active"])
```

Deactivated batches drop out of every FEFO query (`filter(is_active=True)`)
and out of `quantity_on_hand`, so expired stock can never be sold — the
comment in code is explicit: "Never allow expired stock to be sold (spec
§22)". This is enforced independently of, and in addition to, the
consumption-time check in `consume_reservation` described above.

## Low-stock alerting

`InventoryItem.is_low_stock` / `is_out_of_stock` are checked after every
successful `reserve_stock()` call via `_maybe_trigger_low_stock_alert(item)`,
which enqueues `apps.notifications.tasks.notify_low_stock_task.delay(variant_id, available_quantity)`
— currently this task only logs (`logger.info("Low stock alert: ...")`); it
is the integration point where a real staff-facing alert (email/Slack/etc.)
would be added without touching `apps/inventory/services.py`.

`low_stock_threshold` is configurable per `InventoryItem` (default 5) —
there is also a `Product.low_stock_threshold` field in `apps/catalog/models.py`
for product-level default/display purposes, but the authoritative check used
by `reserve_stock` is the per-variant `InventoryItem.low_stock_threshold`.
