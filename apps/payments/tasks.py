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

    now = timezone.now()
    stuck = Payment.objects.filter(
        status__in=[
            PaymentStatus.PENDING,
            PaymentStatus.REQUIRES_ACTION,
            PaymentStatus.AUTHORIZED,
        ],
        # Only routes there is something to ask. Cash on delivery and InstaPay
        # are settled by a person, and their `get_status` echoes our own record
        # back, so including them would be a query that cannot learn anything.
        provider__in=WEBHOOK_PROVIDERS,
        created_at__lt=now - STALE_AFTER,
        created_at__gt=now - GIVE_UP_AFTER,
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
