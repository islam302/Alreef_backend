import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def process_due_subscriptions_task():
    from apps.subscriptions.services import process_due_subscriptions

    results = process_due_subscriptions()
    if results:
        logger.info("Processed %s due subscription(s)", len(results))
    return len(results)
