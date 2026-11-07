"""
WhatsApp through Meta's official WhatsApp Cloud API.

Business-initiated messages — "your order is confirmed", "it has shipped" — may
only be sent as a *template* that Meta has approved beforehand (category
Utility for order updates). The template's wording lives in Meta's WhatsApp
Manager; this module only names the template and fills its {{1}}, {{2}}, ...
placeholders, in order, from `params["body"]`.

    POST https://graph.facebook.com/<version>/<phone_number_id>/messages
    Authorization: Bearer <access token>

A 200 means Meta *accepted* the message, not that it reached the phone: the
"delivered"/"read"/"failed" statuses arrive later on a webhook, which this
shop does not subscribe to yet.

Unofficial WhatsApp Web automation is deliberately not offered: it breaks
WhatsApp's terms and gets the sending number banned.
"""

import logging

import requests
from django.conf import settings

from .whatsapp_base import WhatsAppProvider

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/{version}/{phone_number_id}/messages"

# Cloud API error codes that mean "try again later" rather than "this message
# can never be sent": throttling and Meta-side unavailability. Everything else
# that comes back as a 4xx (template not found, parameter mismatch, number not
# on WhatsApp, an expired token) fails the same way on every retry.
RETRYABLE_ERROR_CODES = {
    4,       # application request limit reached
    80007,   # WhatsApp Business Account rate limit
    130429,  # Cloud API throughput reached
    131000,  # something went wrong (Meta-side)
    131016,  # service temporarily unavailable
    131056,  # too many messages to this one number in a short time
    133004,  # server temporarily unavailable
}

# Meta refuses a text parameter containing a newline or a tab, or more than
# four spaces in a row, and an empty one.
_MAX_PARAM_LENGTH = 1024


def clean_param(value) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text[:_MAX_PARAM_LENGTH] or "-"


class MetaCloudWhatsAppProvider(WhatsAppProvider):
    def __init__(self, *, phone_number_id: str = "", access_token: str = "", api_version: str = "", timeout: int | None = None):
        self.phone_number_id = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID
        self.access_token = access_token or settings.WHATSAPP_ACCESS_TOKEN
        self.api_version = api_version or settings.WHATSAPP_GRAPH_API_VERSION
        self.timeout = timeout or settings.WHATSAPP_TIMEOUT_SECONDS
        if not self.phone_number_id or not self.access_token:
            # Raised rather than failing every send quietly: a deployment that
            # switched the provider on and forgot a credential should find out
            # at the first message, in the task log, not from customers.
            raise NotImplementedError(
                "WHATSAPP_PROVIDER=meta_cloud_api needs WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_ACCESS_TOKEN."
            )

    def build_payload(self, to: str, template_name: str, params: dict) -> dict:
        params = params or {}
        template = {
            "name": template_name,
            "language": {"code": params.get("language") or settings.WHATSAPP_TEMPLATE_LANGUAGE},
        }
        body = [clean_param(value) for value in params.get("body") or []]
        if body:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": value} for value in body]},
            ]
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            # The Cloud API takes the number with its country code and without
            # the plus — the same digits WhatsApp itself uses as the wa_id.
            "to": str(to).lstrip("+"),
            "type": "template",
            "template": template,
        }

    def send_template(self, to: str, template_name: str, params: dict) -> dict:
        url = GRAPH_URL.format(version=self.api_version, phone_number_id=self.phone_number_id)
        payload = self.build_payload(to, template_name, params)
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=self.timeout,
            )
        except (requests.ConnectionError, requests.exceptions.ConnectTimeout) as exc:
            # Never reached Meta: safe to send again.
            return self._failure(f"could not reach the WhatsApp Cloud API ({exc.__class__.__name__})", retryable=True)
        except requests.RequestException as exc:
            # A read timeout, mostly. Meta may well have accepted the message
            # before we stopped waiting, and a retry would send the customer
            # the same notice twice. One missing message is the lesser harm.
            return self._failure(f"no answer from the WhatsApp Cloud API ({exc.__class__.__name__})", retryable=False)

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code == 200 and isinstance(data, dict) and data.get("messages"):
            message_id = (data["messages"][0] or {}).get("id", "")
            return {"success": True, "provider_message_id": message_id, "raw": data, "retryable": False}

        error = data.get("error") if isinstance(data, dict) else None
        if not isinstance(error, dict):
            error = {}
        code = error.get("code")
        detail = error["error_data"].get("details") if isinstance(error.get("error_data"), dict) else ""
        message = f"HTTP {response.status_code}, code {code}: {error.get('message') or 'unexpected response'}"
        if detail:
            message = f"{message} ({detail})"
        retryable = response.status_code == 429 or response.status_code >= 500 or code in RETRYABLE_ERROR_CODES
        return self._failure(message, retryable=retryable, raw=data)

    @staticmethod
    def _failure(message: str, *, retryable: bool, raw=None) -> dict:
        # The token is in the request headers only; nothing here echoes it.
        logger.warning("WhatsApp send failed: %s", message)
        return {"success": False, "provider_message_id": "", "raw": raw, "error": message, "retryable": retryable}
