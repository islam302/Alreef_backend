import logging
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.orders.models import (
    Order,
    OrderStatus,
    OrderStatusHistory,
    ReturnItem,
    ReturnPolicySettings,
    ReturnRequest,
    ReturnRequestStatus,
)

logger = logging.getLogger(__name__)


ORDER_NUMBER_SEQUENCE = "orders_order_number_seq"

# Retained as a last line of defence only. With `nextval()` allocating the
# counter, a collision now requires somebody to have inserted an order number by
# hand; it is not the normal path and must not be relied on as one.
MAX_ORDER_NUMBER_ATTEMPTS = 3


def generate_order_number() -> str:
    """
    The next order number: ORD-YYYYMMDD-00001.

    The counter comes from a database sequence, not from `MAX(order_number) + 1`.
    That earlier read was the cause of a real outage class: two customers
    checking out in the same second read the same highest number, and the second
    INSERT died on the unique constraint, so one of them lost their order to a
    500. A retry loop made it less likely without making it correct — under
    ten-way simultaneity a thread can keep losing.

    `nextval()` takes no lock, so concurrent checkouts never queue behind each
    other, and it does not roll back, so no two callers can ever be handed the
    same value.

    What that costs, stated plainly: an abandoned checkout consumes a number, so
    the series has gaps, and the counter is global rather than restarting daily.
    The number keeps its shape, its length, its uniqueness and its ordering.

    Postgres-specific, deliberately. This project targets Postgres everywhere —
    migrations, health checks and the money fields already assume it.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT nextval('{ORDER_NUMBER_SEQUENCE}')")
        seq = cursor.fetchone()[0]
    return f"ORD-{timezone.now().strftime('%Y%m%d')}-{seq:05d}"


def create_order_with_unique_number(**fields) -> Order:
    """
    Create an Order with a freshly allocated number.

    The sequence makes a clash between two concurrent checkouts impossible, so
    this wrapper exists for the one case it cannot cover: a number inserted
    outside the sequence, by a fixture or by hand. Each attempt gets its own
    savepoint, because an IntegrityError makes the transaction it happened in
    unusable and the surrounding checkout has to survive to try again.
    """
    for attempt in range(MAX_ORDER_NUMBER_ATTEMPTS):
        try:
            with transaction.atomic():
                return Order.objects.create(order_number=generate_order_number(), **fields)
        except IntegrityError as exc:
            # Specific on purpose. Any other constraint means something else is
            # wrong, and looping over it would bury the real error.
            if "order_number" not in str(exc):
                raise
            if attempt == MAX_ORDER_NUMBER_ATTEMPTS - 1:
                logger.error(
                    "order number allocation gave up after %s collisions; a number was "
                    "probably inserted outside the sequence",
                    MAX_ORDER_NUMBER_ATTEMPTS,
                )
                raise
            logger.warning("order number collision outside the sequence, retrying (%s)", attempt + 1)


@transaction.atomic
def transition_status(order: Order, new_status: str, *, changed_by=None, note: str = "") -> Order:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.can_transition_to(new_status):
        raise ApplicationError(
            ErrorCodes.ORDER_INVALID_TRANSITION,
            f"Cannot move order from '{order.status}' to '{new_status}'.",
        )

    from_status = order.status
    order.status = new_status

    now = timezone.now()
    if new_status == OrderStatus.CONFIRMED:
        order.confirmed_at = now
    elif new_status == OrderStatus.SHIPPED:
        order.shipped_at = now
    elif new_status == OrderStatus.DELIVERED:
        order.delivered_at = now
    elif new_status == OrderStatus.CANCELLED:
        order.cancelled_at = now

    order.save()
    OrderStatusHistory.objects.create(order=order, from_status=from_status, to_status=new_status, changed_by=changed_by, note=note)
    _dispatch_status_notification(order, new_status)

    if new_status == OrderStatus.DELIVERED:
        _collect_cash_on_delivery(order, collected_by=changed_by)

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        actor=changed_by, action=AuditAction.STATUS_CHANGE, model_name="Order", object_id=order.order_number,
        changes={"from": from_status, "to": new_status, "note": note},
    )
    return order


@transaction.atomic
def cancel_order(order: Order, *, reason: str, cancelled_by=None) -> Order:
    if not order.can_cancel():
        raise ApplicationError(ErrorCodes.ORDER_CANNOT_CANCEL, f"Order in status '{order.status}' cannot be cancelled.")

    from apps.inventory.models import StockReservation, StockReservationStatus
    from apps.inventory.services import release_reservation

    for reservation in StockReservation.objects.filter(order=order, status=StockReservationStatus.ACTIVE):
        release_reservation(reservation)
    _restock_consumed(order)

    # The coupon use was reserved when the order was placed; a cancelled order
    # never happened, so the use goes back to the code's limits.
    from apps.coupons.services import release_usage

    release_usage(order=order)

    order = transition_status(order, OrderStatus.CANCELLED, changed_by=cancelled_by, note=reason)
    order.cancel_reason = reason
    order.save(update_fields=["cancel_reason"])

    # Money already taken online stays taken until someone refunds it. Say so on
    # the payment, where the refund button is, instead of leaving it to memory.
    from apps.payments.services import flag_paid_cancellation

    flag_paid_cancellation(order)
    return order


def _restock_consumed(order: Order) -> None:
    """
    Put back what a confirmed order had already taken from the shelf.

    Confirmation consumes the reservation (the units leave their batch), and
    every COD order is confirmed at checkout, so releasing only *active*
    reservations on cancel lost the stock of every cancelled confirmed order.
    The units go back into the batch they came from, so expiry dates stay true.
    Runs once per order: an order can only be cancelled once.
    """
    from apps.inventory.models import (
        Batch,
        InventoryMovement,
        InventoryMovementType,
        StockReservation,
        StockReservationStatus,
    )

    consumed = StockReservation.objects.filter(
        order=order, status=StockReservationStatus.CONSUMED, batch__isnull=False
    )
    for reservation in consumed:
        batch = Batch.objects.select_for_update().get(pk=reservation.batch_id)
        batch.quantity += reservation.quantity
        batch.save(update_fields=["quantity"])
        InventoryMovement.objects.create(
            item=reservation.item, batch=batch, movement_type=InventoryMovementType.RETURN,
            quantity_delta=reservation.quantity, reference=str(order.order_number),
        )


def _collect_cash_on_delivery(order: Order, *, collected_by=None):
    """
    Delivery is when a cash-on-delivery payment actually becomes ours.

    Hooked to the status transition rather than to one dashboard button, so it
    holds for every route that can mark an order delivered — the admin API, the
    Django admin, and any courier integration added later. Nothing else in the
    system may capture a COD payment.

    Failures here are logged, never raised: the parcel *has* been delivered, and
    refusing that fact because a bookkeeping step stumbled would leave the order
    in a state that contradicts the physical world. An uncaptured COD payment is
    visible in the dashboard and fixable; a delivery we refused to record is not.
    """
    from apps.payments.models import Payment, PaymentStatus

    payment = (
        Payment.objects.filter(order=order, provider="cod")
        .exclude(status__in=[PaymentStatus.CANCELLED, PaymentStatus.FAILED])
        .order_by("-created_at")
        .first()
    )
    if payment is None or payment.status == PaymentStatus.CAPTURED:
        return

    from apps.payments.services import record_cod_collection

    try:
        record_cod_collection(
            payment, collected_by=collected_by, note="Collected on delivery."
        )
    except Exception:
        logger.exception(
            "Order %s was delivered but its COD payment %s could not be marked collected.",
            order.order_number,
            payment.id,
        )


def order_email_context(order: Order) -> dict:
    """What every order email may use. Existing keys keep their names."""
    from apps.orders.guest import order_url_for

    return {
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "total": str(order.grand_total.amount),
        "currency": order.currency,
        "order_url": order_url_for(order),
        "is_guest": order.user_id is None,
    }


# Status -> (notification event, template). The subjects live with the templates
# in apps.notifications.services.EMAIL_SUBJECTS.
STATUS_EMAILS = {
    # Here rather than in checkout's confirm_order, because this is the one
    # place every route to CONFIRMED passes through — checkout, a gateway
    # callback, staff confirming a transfer from the dashboard — and it fires
    # only on the transition itself. confirm_order runs again when cash is
    # collected or a callback is re-delivered, and each of those used to send
    # the customer another "order confirmed".
    OrderStatus.CONFIRMED: ("order_created", "order_confirmation"),
    OrderStatus.SHIPPED: ("order_shipped", "order_shipped"),
    OrderStatus.DELIVERED: ("order_delivered", "order_delivered"),
    # Staff cancelling an order used to leave the customer to find out when the
    # courier never came.
    OrderStatus.CANCELLED: ("order_cancelled", "order_cancelled"),
}


def _dispatch_status_notification(order: Order, new_status: str):
    from apps.notifications.services import queue_email

    entry = STATUS_EMAILS.get(new_status)
    if not entry:
        return
    event, template_name = entry
    context = order_email_context(order)
    if new_status == OrderStatus.CANCELLED:
        # No reason in the email, deliberately. The reason is typed by staff
        # into a dashboard field that reads as an internal note ("duplicate",
        # "fake number"), and nothing there tells them a customer will read it.
        # What the customer does need to know is whether money is involved.
        from apps.payments.models import PaymentStatus

        context["was_paid"] = order.payments.filter(
            status__in=[PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED]
        ).exists()
    queue_email(
        to=order.customer_email, subject=template_name, template_name=template_name,
        context=context, event=event, user_id=order.user_id,
    )


@transaction.atomic
def create_return_request(*, order: Order, user, reason: str, description: str, order_item_quantities: dict) -> ReturnRequest:
    """order_item_quantities: {order_item_id: quantity}"""
    policy = ReturnPolicySettings.get_solo()

    if order.status != OrderStatus.DELIVERED:
        raise ApplicationError(ErrorCodes.ORDER_INVALID_TRANSITION, "Only delivered orders can be returned.")
    if order.delivered_at and timezone.now() > order.delivered_at + timedelta(days=policy.return_window_days):
        raise ApplicationError(ErrorCodes.ORDER_INVALID_TRANSITION, f"Return window of {policy.return_window_days} days has passed.")
    if reason == "customer_change_of_mind" and not policy.allow_change_of_mind_returns:
        raise ApplicationError(ErrorCodes.ORDER_INVALID_TRANSITION, "Change-of-mind returns are not accepted.")

    return_request = ReturnRequest.objects.create(
        order=order, user=user, reason=reason, description=description,
        status=ReturnRequestStatus.REQUESTED if policy.require_approval else ReturnRequestStatus.APPROVED,
    )
    for order_item_id, quantity in order_item_quantities.items():
        order_item = order.items.filter(pk=order_item_id).first()
        if not order_item or quantity > order_item.quantity:
            raise ApplicationError(ErrorCodes.VALIDATION_ERROR, f"Invalid order item/quantity: {order_item_id}")
        ReturnItem.objects.create(return_request=return_request, order_item=order_item, quantity=quantity)

    transition_status(order, OrderStatus.RETURN_REQUESTED, changed_by=user, note=f"Return requested: {reason}")
    return return_request


@transaction.atomic
def decide_return_request(*, return_request: ReturnRequest, approve: bool, decided_by, note: str = ""):
    if return_request.status != ReturnRequestStatus.REQUESTED:
        raise ApplicationError(
            ErrorCodes.ORDER_INVALID_TRANSITION,
            f"This return is already {return_request.status} and cannot be decided again.",
        )
    return_request.status = ReturnRequestStatus.APPROVED if approve else ReturnRequestStatus.REJECTED
    return_request.resolution_note = note
    return_request.save(update_fields=["status", "resolution_note"])

    if not approve:
        transition_status(return_request.order, OrderStatus.DELIVERED, changed_by=decided_by, note="Return request rejected")
    return return_request


@transaction.atomic
def complete_return_and_refund(*, return_request: ReturnRequest, processed_by, destination="original_method"):
    from apps.payments.services import create_refund

    # Only an approved return is refunded. The dashboard hides the button
    # otherwise, but the server must not rely on that: completing a rejected
    # return while a later one is open used to refund the rejected items.
    if return_request.status != ReturnRequestStatus.APPROVED:
        raise ApplicationError(
            ErrorCodes.ORDER_INVALID_TRANSITION,
            f"Only an approved return can be completed; this one is {return_request.status}.",
        )
    order = return_request.order
    return_request.status = ReturnRequestStatus.ITEM_RECEIVED
    return_request.save(update_fields=["status"])
    order = transition_status(order, OrderStatus.RETURNED, changed_by=processed_by)

    refund_amount = sum(
        (item.order_item.unit_price * item.quantity for item in return_request.items.select_related("order_item")),
        start=Money(0, order.currency),
    )
    payment = order.payments.order_by("-created_at").first()
    if payment and refund_amount.amount > 0:
        create_refund(payment=payment, amount=refund_amount, reason=f"Return #{return_request.id}", destination=destination)

    new_status = OrderStatus.PARTIALLY_REFUNDED if refund_amount < order.grand_total else OrderStatus.REFUNDED
    order = transition_status(order, new_status, changed_by=processed_by)
    return_request.status = ReturnRequestStatus.REFUNDED
    return_request.save(update_fields=["status"])
    return return_request
