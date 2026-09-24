import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def release_expired_reservations_task():
    from apps.inventory.services import release_expired_reservations

    count = release_expired_reservations()
    if count:
        logger.info("Released %s expired stock reservation(s)", count)
    return count


@shared_task
def deactivate_expired_batches_task():
    """Never allow expired stock to be sold (spec §22): flips is_active off,
    and logs an EXPIRED movement for audit."""
    from apps.inventory.models import InventoryMovement, InventoryMovementType
    from apps.inventory.services import get_expired_batches

    expired = get_expired_batches()
    count = 0
    for batch in expired:
        InventoryMovement.objects.create(
            item=batch.item, batch=batch, movement_type=InventoryMovementType.EXPIRED,
            quantity_delta=-batch.quantity, reference=batch.batch_number,
            notes="Auto-deactivated: batch reached expiration_date.",
        )
        batch.is_active = False
        batch.save(update_fields=["is_active"])
        count += 1
    if count:
        logger.info("Deactivated %s expired batch(es)", count)
    return count
