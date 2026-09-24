import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.notifications.models import NotificationChannel
from apps.notifications.services import (
    create_in_app_notification,
    log_delivery,
    send_email_now,
    send_sms_now,
    send_whatsapp_now,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, *, to: str, subject: str, template_name: str, context: dict, event: str = "", user_id=None):
    result = send_email_now(to=to, subject=subject, template_name=template_name, context=context)
    log_delivery(event=event, channel=NotificationChannel.EMAIL, recipient=to, success=result["success"],
                 user_id=user_id, error_message=result.get("error", ""))
    if not result["success"]:
        raise self.retry(exc=Exception(result.get("error", "email send failed")))
    return result


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_sms_task(self, *, to: str, message: str, event: str = "", user_id=None):
    result = send_sms_now(to=to, message=message)
    log_delivery(event=event, channel=NotificationChannel.SMS, recipient=to, success=result["success"], user_id=user_id,
                 provider_message_id=result.get("provider_message_id", ""))
    if not result["success"]:
        raise self.retry(exc=Exception("sms send failed"))
    return result


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_whatsapp_task(self, *, to: str, template_name: str, params: dict, event: str = "", user_id=None):
    result = send_whatsapp_now(to=to, template_name=template_name, params=params)
    log_delivery(event=event, channel=NotificationChannel.WHATSAPP, recipient=to, success=result["success"], user_id=user_id,
                 provider_message_id=result.get("provider_message_id", ""))
    if not result["success"]:
        raise self.retry(exc=Exception("whatsapp send failed"))
    return result


@shared_task
def create_in_app_notification_task(*, user_id, event: str, title: str, body: str = "", data: dict | None = None):
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("create_in_app_notification_task: user %s not found", user_id)
        return None
    notification = create_in_app_notification(user=user, event=event, title=title, body=body, data=data)
    return notification.id


@shared_task
def notify_low_stock_task(product_variant_id, quantity_available):
    """Triggered by inventory signals when stock crosses the low-stock threshold."""
    logger.info("Low stock alert: variant=%s available=%s", product_variant_id, quantity_available)


@shared_task
def notify_expiration_alerts_task():
    """Celery Beat: scans inventory batches nearing/at expiry (7/14/30 day thresholds)."""
    from apps.inventory.services import get_batches_nearing_expiration

    for threshold_days, batches in get_batches_nearing_expiration().items():
        for batch in batches:
            logger.info("Expiration alert: batch=%s expires_in=%sd", batch.batch_number, threshold_days)


@shared_task
def process_abandoned_carts_task():
    """Celery Beat: finds carts inactive for ABANDONED_CART_HOURS and notifies once."""
    from apps.carts.services import get_abandoned_carts

    for cart in get_abandoned_carts():
        if cart.user and cart.user.email:
            send_email_task.delay(
                to=cart.user.email,
                subject="You left something in your cart",
                template_name="abandoned_cart",
                context={"cart_id": str(cart.id)},
                event="abandoned_cart",
                user_id=cart.user_id,
            )
        cart.abandoned_notified_at = timezone.now()
        cart.save(update_fields=["abandoned_notified_at"])
