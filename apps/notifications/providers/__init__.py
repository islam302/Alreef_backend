from django.conf import settings

from .sms_base import ConsoleSMSProvider, SMSProvider
from .whatsapp_base import ConsoleWhatsAppProvider, WhatsAppProvider

__all__ = ["SMSProvider", "WhatsAppProvider", "get_sms_provider", "get_whatsapp_provider"]


def get_sms_provider() -> SMSProvider:
    """
    Provider abstraction (spec §50): never hard-code one SMS vendor.
    Add a new backend by implementing SMSProvider.send() and registering it
    here; select via SMS_PROVIDER env var.
    """
    provider = getattr(settings, "SMS_PROVIDER", "console")
    if provider == "console":
        return ConsoleSMSProvider()
    # Real vendors (Vonage, Twilio, Msegat, ...) require credentials + their
    # official SDK/API docs before being wired in here. See docs/architecture/notifications.md.
    raise NotImplementedError(
        f"SMS provider '{provider}' is not implemented. Only 'console' ships out of the box; "
        "implement an SMSProvider subclass with verified vendor documentation to add one."
    )


def get_whatsapp_provider() -> WhatsAppProvider:
    provider = getattr(settings, "WHATSAPP_PROVIDER", "console")
    if provider == "console":
        return ConsoleWhatsAppProvider()
    raise NotImplementedError(
        f"WhatsApp provider '{provider}' is not implemented. Only 'console' ships out of the box; "
        "implement a WhatsAppProvider subclass against the official WhatsApp Cloud API docs to add one."
    )
