import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from apps.notifications.providers import get_sms_provider, get_whatsapp_provider

logger = logging.getLogger(__name__)


def send_email_now(*, to: str, subject: str, template_name: str, context: dict | None = None) -> dict:
    """Renders templates/emails/{template_name}.html (+ .txt fallback) and sends synchronously."""
    context = context or {}
    context.setdefault("site_name", settings.SITE_NAME)
    context.setdefault("frontend_url", settings.FRONTEND_URL)
    html_body = render_to_string(f"emails/{template_name}.html", context)
    try:
        text_body = render_to_string(f"emails/{template_name}.txt", context)
    except Exception:
        text_body = html_body

    message = EmailMultiAlternatives(subject=subject, body=text_body, to=[to])
    message.attach_alternative(html_body, "text/html")
    try:
        message.send(fail_silently=False)
        return {"success": True}
    except Exception as exc:
        logger.exception("Failed to send email to %s", to)
        return {"success": False, "error": str(exc)}


def send_sms_now(*, to: str, message: str) -> dict:
    return get_sms_provider().send(to=to, message=message)


def send_whatsapp_now(*, to: str, template_name: str, params: dict) -> dict:
    return get_whatsapp_provider().send_template(to=to, template_name=template_name, params=params)


def create_in_app_notification(*, user, event: str, title: str, body: str = "", data: dict | None = None):
    from apps.notifications.models import Notification

    return Notification.objects.create(user=user, event=event, title=title, body=body, data=data or {})


def log_delivery(*, event: str, channel: str, recipient: str, success: bool, user_id=None, provider_message_id: str = "", error_message: str = ""):
    from apps.notifications.models import NotificationLog

    NotificationLog.objects.create(
        user_id=user_id,
        event=event,
        channel=channel,
        recipient=recipient,
        success=success,
        provider_message_id=provider_message_id,
        error_message=error_message,
    )
