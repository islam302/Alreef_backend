import logging
from datetime import timedelta

from celery import shared_task

logger = logging.getLogger(__name__)

#: How long a payment may sit unsettled before the sweep bothers a provider
#: about it. Short enough that a lost webhook is found within the hour, long
#: enough that the sweep is not racing webhooks that are simply in flight.
STALE_AFTER = timedelta(minutes=10)

#: Payments older than this are not chased any more. An abandoned checkout is
#: never coming back, and asking a provider about it every fifteen minutes
#: forever is a slow leak of rate limit.
GIVE_UP_AFTER = timedelta(days=14)

#: How long after a decline or a cancellation we still ask whether a success
#: followed. A customer retrying on the same Paymob page does so within minutes;
#: two days covers a wallet confirmation that crawls in overnight.
LATE_SUCCESS_WINDOW = timedelta(days=2)


@shared_task
def reconcile_pending_payments_task():
    """
    Find payments a webhook never settled, and ask the provider what happened.

    The safety net behind every gateway integration: providers drop callbacks,
    our endpoint can be down for a deploy, and a customer who paid must not be
    left with an unconfirmed order because of either.

    WHAT CHANGED AND WHY. This task used to compare `result.status` against the
    literal strings `"captured"` and `"failed"`, which silently stopped matching
    anything the moment a gateway with a vocabulary of its own was added. It also
    skipped the amount check and wrote statuses without consulting the state
    machine. It now delegates to `services.reconcile_payment`, which maps the
    provider's answer through an explicit table, verifies the amount agrees with
    ours, checks the transition is legal, and confirms the order.

    It also used to look only at PENDING and AUTHORIZED, missing
    `requires_action` — which is the state every redirect payment actually sits
    in, so the sweep found nothing it was built to find.
    """
    from django.utils import timezone

    from apps.payments.models import Payment, PaymentStatus
    from apps.payments.services import reconcile_payment
    from apps.payments.webhooks import WEBHOOK_PROVIDERS

    from django.db.models import Q

    now = timezone.now()
    stuck = Payment.objects.filter(
        Q(
            status__in=[
                PaymentStatus.PENDING,
                PaymentStatus.REQUIRES_ACTION,
                PaymentStatus.AUTHORIZED,
            ],
            created_at__gt=now - GIVE_UP_AFTER,
        )
        # A refund that timed out, however old the payment: only the gateway
        # can say whether it happened, and until it does nothing else can be refunded.
        | Q(status=PaymentStatus.REFUND_PENDING)
        # A success can land behind a decline (the customer tried another card)
        # or behind a cancellation, and if its callback is lost too, only asking
        # finds it.
        | Q(
            status__in=[PaymentStatus.FAILED, PaymentStatus.CANCELLED],
            created_at__gt=now - LATE_SUCCESS_WINDOW,
        ),
        # Only routes there is something to ask. Cash on delivery and InstaPay
        # are settled by a person, and their `get_status` echoes our own record
        # back, so including them would be a query that cannot learn anything.
        provider__in=WEBHOOK_PROVIDERS,
        created_at__lt=now - STALE_AFTER,
    ).order_by("created_at")

    reconciled = 0
    for payment in stuck.iterator():
        before = payment.status
        try:
            payment = reconcile_payment(payment)
        except Exception:
            # One unreachable provider, or one payment whose amount disagrees,
            # must not stop the sweep for everybody else. The amount mismatch in
            # particular is logged with a traceback because it is an incident.
            logger.exception("Could not reconcile payment %s", payment.id)
            continue
        if payment.status != before:
            reconciled += 1

    if reconciled:
        logger.info("Reconciled %s pending payment(s)", reconciled)
    return reconciled


@shared_task
def expire_stale_payments_task():
    """
    Close out payments nobody is ever going to complete.

    A Paymob intention does not last forever, and an InstaPay transfer that was
    never made is never going to be. Without this they sit in `requires_action`
    indefinitely, which makes the dashboard's "needs chasing" filter useless —
    the real cases drown in months of abandoned checkouts.

    Deliberately conservative: it only touches payments past `GIVE_UP_AFTER`, and
    only ones we have already stopped reconciling. A payment that a provider might
    still settle is left alone, because marking it expired here and having the
    money arrive later is the worse mistake.
    """
    from django.utils import timezone

    from apps.payments.models import Payment, PaymentStatus
    from apps.payments.services import mark_payment_expired

    cutoff = timezone.now() - GIVE_UP_AFTER
    abandoned = Payment.objects.filter(
        status__in=[PaymentStatus.PENDING, PaymentStatus.REQUIRES_ACTION],
        created_at__lt=cutoff,
        # Cash on delivery has no expiry: an undelivered order is a fulfilment
        # problem, not a payment that timed out. InstaPay *is* included — a
        # transfer nobody made in a fortnight is not coming.
    ).exclude(provider="cod")

    expired = 0
    for payment in abandoned.iterator():
        try:
            mark_payment_expired(payment, reason="No payment received before expiry.")
            expired += 1
        except Exception:
            logger.exception("Could not expire payment %s", payment.id)

    if expired:
        logger.info("Expired %s abandoned payment(s)", expired)
    return expired


#: How long an online order may wait for its payment before its stock goes
#: back on sale. Long enough for a slow wallet confirmation; short enough that
#: abandoned card checkouts do not quietly sell out a product.
UNPAID_ORDER_HOLD = timedelta(hours=24)


@shared_task
def release_unpaid_orders_task():
    """
    Cancel online orders that were never paid, so their stock returns.

    Order reservations do not expire, so before this an abandoned card or
    wallet checkout held its jars for good. Each order's payment is asked about
    one last time first; one that turns out paid is confirmed instead. If a
    success still arrives after the cancel, it is recorded and flagged for a
    refund (services.mark_payment_captured), so cancelling never loses money.

    InstaPay is left alone: a person checks those transfers, and the shop, not a
    timer, decides when to give up on one. A gateway that cannot be reached
    leaves the order alone too: not knowing is not the same as unpaid.
    """
    from django.utils import timezone

    from apps.core.error_codes import ApplicationError
    from apps.orders.models import Order, OrderStatus
    from apps.orders.services import cancel_order
    from apps.payments import state
    from apps.payments.services import mark_payment_expired, reconcile_payment
    from apps.payments.webhooks import WEBHOOK_PROVIDERS

    cutoff = timezone.now() - UNPAID_ORDER_HOLD
    orders = Order.objects.filter(
        status=OrderStatus.PENDING,
        created_at__lt=cutoff,
        payments__provider__in=WEBHOOK_PROVIDERS,
    ).distinct()

    released = 0
    for order in orders.iterator():
        try:
            payments = list(order.payments.filter(provider__in=WEBHOOK_PROVIDERS))
            unknown = False
            for payment in payments:
                if payment.status in state.IN_FLIGHT or payment.status in state.LATE_CAPTURE_FROM:
                    try:
                        payment = reconcile_payment(payment)
                    except ApplicationError:
                        unknown = True
                        continue
                if payment.status in state.IN_FLIGHT:
                    mark_payment_expired(payment, reason="Not paid within 24 hours.")
            if unknown:
                continue
            order.refresh_from_db()
            if order.status != OrderStatus.PENDING:
                continue  # It was paid after all, and is now confirmed.
            if order.payments.filter(status__in=state.SETTLED).exists():
                continue
            cancel_order(order, reason="Not paid within 24 hours.")
            released += 1
        except Exception:
            logger.exception("Could not release unpaid order %s", order.pk)

    if released:
        logger.info("Released %s unpaid order(s)", released)
    return released
