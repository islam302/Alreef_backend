import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def reconcile_pending_payments_task():
    """
    Celery Beat: for gateway payments left PENDING/AUTHORIZED beyond a
    reasonable window (missed webhook), polls the provider's get_status and
    reconciles. COD payments are intentionally excluded — they resolve on
    delivery, not via polling.
    """
    from apps.payments.models import Payment, PaymentStatus
    from apps.payments.providers import get_payment_provider
    from apps.payments.services import mark_payment_captured, mark_payment_failed

    stuck = Payment.objects.filter(status__in=[PaymentStatus.PENDING, PaymentStatus.AUTHORIZED]).exclude(provider="cod")
    reconciled = 0
    for payment in stuck:
        try:
            provider = get_payment_provider(payment.provider)
            result = provider.get_status(payment=payment)
        except NotImplementedError:
            continue
        if result.status == "captured":
            mark_payment_captured(payment)
            reconciled += 1
        elif result.status == "failed":
            mark_payment_failed(payment)
            reconciled += 1
    if reconciled:
        logger.info("Reconciled %s pending payment(s)", reconciled)
    return reconciled
