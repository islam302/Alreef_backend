import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class SMSProvider(ABC):
    @abstractmethod
    def send(self, to: str, message: str) -> dict:
        """Returns {"success": bool, "provider_message_id": str | None, "raw": Any}."""
        raise NotImplementedError


class ConsoleSMSProvider(SMSProvider):
    """Development/testing fallback — logs instead of sending a real SMS."""

    def send(self, to: str, message: str) -> dict:
        logger.info("[SMS -> %s] %s", to, message)
        return {"success": True, "provider_message_id": "console-sms", "raw": None}
