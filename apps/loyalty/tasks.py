import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def expire_stale_points_task():
    from apps.loyalty.services import expire_stale_points

    count = expire_stale_points()
    if count:
        logger.info("Expired loyalty points for %s account(s)", count)
    return count
