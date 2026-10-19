import logging
from functools import partial

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from apps.notifications.providers import get_sms_provider, get_whatsapp_provider

logger = logging.getLogger(__name__)


# The subject every customer email goes out with, keyed by template.
#
# The store is Arabic and its emails were not: callers passed English subjects
# ("Order confirmed", "Payment failed") from wherever they happened to send
# from — checkout, orders, payments, the auth views. Holding the wording here,
# next to the templates it belongs to, means one place decides what a customer
# reads in their inbox, and a caller's `subject` is only the fallback for a
# template this table does not know. `{order_number}` and the other fields are
# filled from the email's own context; a missing one leaves the subject without
# it rather than failing the send.
EMAIL_SUBJECTS = {
    "order_confirmation": "طلبك اتأكد — رقم {order_number}",
    "payment_confirmation": "وصلنا الدفع — طلب رقم {order_number}",
    "payment_failed": "الدفع ماكملش — طلب رقم {order_number}",
    "refund": "رجّعنالك فلوسك — طلب رقم {order_number}",
    "order_shipped": "طلبك خرج للشحن — رقم {order_number}",
    "order_delivered": "طلبك وصل — رقم {order_number}",
    "order_cancelled": "طلبك اتلغى — رقم {order_number}",
    "payment_instructions": "باقي خطوة: حوّل قيمة طلب رقم {order_number}",
    "verify_email": "أكّد إيميلك",
    "reset_password": "إعادة تعيين كلمة السر",
    "welcome": "أهلاً بيك في الريف",
    "abandoned_cart": "سلتك لسه مستنياك",
}


class _SafeFormat(dict):
    def __missing__(self, key):
        return ""


def subject_for(template_name: str, fallback: str, context: dict) -> str:
    template = EMAIL_SUBJECTS.get(template_name)
    if not template:
        return str(fallback)
    return template.format_map(_SafeFormat(context)).strip(" —")


def queue_email(**task_kwargs) -> None:
    """
    Queue an email to go out once the surrounding transaction has committed.

    Calling `send_email_task.delay` inside an atomic block had two failure modes,
    both real at checkout. A transaction that rolled back — stock ran out on the
    last line, a gateway refused — had already queued "your order is confirmed"
    for an order that no longer existed. And the other way round: with Redis
    down, `.delay` raised inside the block and took the whole checkout with it,
    so a customer lost an order the database was perfectly able to take because
    the *email* could not be queued.

    `on_commit` fixes the first: nothing is queued unless the order is real.
    Catching in the callback fixes the second: by then the order is committed,
    and a broker outage costs an email, logged loudly, not the order. Outside a
    transaction `on_commit` runs the callback at once, so callers need not care
    which case they are in.
    """
    if not task_kwargs.get("to"):
        # A guest who left no email address. Nothing to send, and nothing to log
        # as a failure: we were never asked to reach them this way.
        return
    transaction.on_commit(partial(_dispatch_email, task_kwargs))


def _dispatch_email(task_kwargs: dict) -> None:
    from apps.notifications.tasks import send_email_task

    try:
        send_email_task.delay(**task_kwargs)
    except Exception:
        logger.exception(
            "could not queue the '%s' email (event %s); the broker is probably unreachable",
            task_kwargs.get("template_name"), task_kwargs.get("event"),
        )


def send_email_now(*, to: str, subject: str, template_name: str, context: dict | None = None) -> dict:
    """Renders templates/emails/{template_name}.html (+ .txt fallback) and sends synchronously."""
    context = context or {}
    context.setdefault("site_name", settings.SITE_NAME)
    context.setdefault("frontend_url", settings.FRONTEND_URL)
    subject = subject_for(template_name, subject, context)
    html_body = render_to_string(f"emails/{template_name}.html", context)
    try:
        text_body = render_to_string(f"emails/{template_name}.txt", context)
    except Exception:
        # No hand-written plain-text version: derive one, rather than sending the
        # raw HTML as the text part, which is what a text-only client then shows.
        text_body = strip_tags(html_body).strip()

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
