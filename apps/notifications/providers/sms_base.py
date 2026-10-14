import logging
import re
from abc import ABC, abstractmethod

from django.conf import settings

logger = logging.getLogger(__name__)


class SMSProvider(ABC):
    @abstractmethod
    def send(self, to: str, message: str) -> dict:
        """Returns {"success": bool, "provider_message_id": str | None, "raw": Any}."""
        raise NotImplementedError


_DIGITS = re.compile(r"\d")


def mask_digits(message: str) -> str:
    """Every digit replaced, so a one-time code survives as its shape only."""
    return _DIGITS.sub("•", str(message))


class ConsoleSMSProvider(SMSProvider):
    """
    Development/testing fallback — logs instead of sending a real SMS.

    The message body is logged in full only under DEBUG. Everything this shop
    sends by SMS is a verification code, and outside a developer's machine the
    log is a shared, long-lived place (container logs, an aggregator) — writing
    "Your verification code is 482913" there hands the code to whoever can read
    it, which is the attack the code exists to stop. With DEBUG off the digits
    are masked: the log still shows that a message went out and to whom.
    """

    def send(self, to: str, message: str) -> dict:
        body = message if settings.DEBUG else mask_digits(message)
        logger.info("[SMS -> %s] %s", to, body)
        return {"success": True, "provider_message_id": "console-sms", "raw": None}
