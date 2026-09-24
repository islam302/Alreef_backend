import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class WhatsAppProvider(ABC):
    @abstractmethod
    def send_template(self, to: str, template_name: str, params: dict) -> dict:
        """Returns {"success": bool, "provider_message_id": str | None, "raw": Any}."""
        raise NotImplementedError


class ConsoleWhatsAppProvider(WhatsAppProvider):
    """Development/testing fallback. A real integration uses the official
    WhatsApp Cloud API (Meta) with WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ACCESS_TOKEN
    from the environment — never hard-code credentials in source."""

    def send_template(self, to: str, template_name: str, params: dict) -> dict:
        logger.info("[WhatsApp -> %s] template=%s params=%s", to, template_name, params)
        return {"success": True, "provider_message_id": "console-whatsapp", "raw": None}
