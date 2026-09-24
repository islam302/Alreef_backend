# Returns

Return/refund requests are modeled on the `orders` app (`ReturnRequest`,
`ReturnItem`) — there is no separate `returns` Django app; policy fields
live in `ReturnPolicySettings` (a singleton editable in Django admin) so
legal/business rules are never hard-coded (spec §39).

## Create a return request — `POST /api/v1/orders/{order_number}/returns/`

Auth required, must be the order's owner.

```json
{
  "reason": "damaged",
  "description": "Jar arrived broken in transit",
  "items": [{ "order_item_id": 1, "quantity": 1 }]
}
```

`reason` is one of:

| Value | Meaning |
|---|---|
| `damaged` | Damaged |
| `wrong_product` | Wrong product |
| `missing_product` | Missing product |
| `expired_product` | Expired product |
| `quality_issue` | Quality issue |
| `customer_change_of_mind` | Change of mind |

**Eligibility rules** (enforced in `apps.orders.services.create_return_request`):
- Order status must be `DELIVERED`.
- Must be within `ReturnPolicySettings.return_window_days` of `delivered_at`
  (default 14 days) — otherwise `ORDER_INVALID_TRANSITION`.
- `customer_change_of_mind` is rejected if
  `ReturnPolicySettings.allow_change_of_mind_returns` is `False`.
- Requested quantity per item cannot exceed the quantity actually purchased
  on that `OrderItem`.

On success, the order transitions `DELIVERED → RETURN_REQUESTED` (see the
state machine in `docs/api/orders.md`), and the return's initial status is
`requested` if `ReturnPolicySettings.require_approval` is `True` (the
default), otherwise it's auto-`approved`.

## List your return requests — `GET /api/v1/orders/returns/`

Auth required. Returns every return request you've filed, across all your
orders.

## Return lifecycle (staff-managed, via Django admin)

```
requested → approved → item_received → refunded
         └→ rejected                 (order returns to DELIVERED)
```

Staff act on return requests from the Django admin `ReturnRequest` list via
bulk actions: **Approve**, **Reject**, and **Mark item received & issue
refund** (the last one calls `apps.orders.services.complete_return_and_refund`,
which creates a `Refund` against the order's most recent `Payment` for the
sum of the returned items' line totals, and moves the order to `REFUNDED`
or `PARTIALLY_REFUNDED` depending on whether the whole order value was
covered).

There is currently no public "approve/reject" API endpoint for staff — this
is intentionally admin-only for now; add a staff-scoped API view following
the same pattern as `apps/payments/views.RefundCreateView` if a separate
admin frontend needs it instead of Django admin.
