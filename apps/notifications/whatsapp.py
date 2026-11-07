"""
Order updates on WhatsApp: which template, filled with what, sent to which number.

The channel-agnostic part (the Celery task, the delivery log) lives in
`tasks.py` and `services.py`; the vendor part in `providers/meta_cloud.py`.
This module is the glue the order code calls: it looks the message up in
`settings.WHATSAPP_ORDER_TEMPLATES`, fills the template's parameters from the
order's fields, and queues the send for after the transaction commits — the
same rule, for the same reasons, as `services.queue_email`.
"""

import logging
import re
from functools import partial

import phonenumbers
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

# Arabic-Indic and Persian digits, which an Arabic keyboard types by default.
_EASTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NOT_DIALLED = re.compile(r"[^\d+]")


def to_e164(raw, region: str | None = None) -> str:
    """
    A phone number as WhatsApp addresses it (+201001234567), or "" if it is not one.

    Takes what customers actually type: 01001234567, 1001234567, 0020 100 123
    4567, 201001234567, +20-100-123-4567, the same in Arabic digits, and Gulf
    numbers with their own country code. A landline is refused: it cannot
    receive WhatsApp, and Meta would bounce the send.
    """
    text = _NOT_DIALLED.sub("", str(raw or "").translate(_EASTERN_DIGITS))
    if text.startswith("00"):
        text = "+" + text[2:]
    elif not text.startswith("+") and text.startswith("20") and len(text) == 12:
        # 20 + ten national digits: an Egyptian number written internationally
        # without its plus. A national one starts with 0 and is eleven long.
        text = "+" + text
    try:
        parsed = phonenumbers.parse(text, region or settings.PHONENUMBER_DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(parsed):
        return ""
    if phonenumbers.number_type(parsed) == phonenumbers.PhoneNumberType.FIXED_LINE:
        return ""
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def template_for(message: str) -> dict | None:
    """The configured template for `message`, or None while it is switched off."""
    entry = (getattr(settings, "WHATSAPP_ORDER_TEMPLATES", None) or {}).get(message) or {}
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    params = [str(p).strip() for p in entry.get("params") or [] if str(p).strip()]
    return {"name": name, "params": params}


def queue_whatsapp_template(*, to: str, message: str, fields: dict, event: str, user_id=None) -> bool:
    """
    Queue `message` to `to` once the surrounding transaction commits.

    Returns whether anything was queued. Nothing is when the message has no
    template configured (the default: WhatsApp stays silent until a template is
    approved and named), when there is no phone, or when the template asks for a
    field this message cannot fill — that last one a configuration mistake,
    logged as an error, that must never cost the customer their order.
    """
    template = template_for(message)
    if template is None or not to:
        return False
    missing = [name for name in template["params"] if name not in fields]
    if missing:
        logger.error(
            "WhatsApp template '%s' (%s) asks for %s, which this message does not have; not sent",
            template["name"], message, ", ".join(missing),
        )
        return False
    task_kwargs = {
        "to": str(to),
        "template_name": template["name"],
        "params": {"body": [str(fields[name]) for name in template["params"]]},
        "event": event,
        "user_id": user_id,
    }
    transaction.on_commit(partial(_dispatch_whatsapp, task_kwargs))
    return True


def _dispatch_whatsapp(task_kwargs: dict) -> None:
    from apps.notifications.tasks import send_whatsapp_task

    try:
        send_whatsapp_task.delay(**task_kwargs)
    except Exception:
        logger.exception(
            "could not queue the '%s' WhatsApp message (event %s); the broker is probably unreachable",
            task_kwargs.get("template_name"), task_kwargs.get("event"),
        )
