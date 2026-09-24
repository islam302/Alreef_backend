"""
The one way this app talks to a payment gateway over HTTP.

Adapters do not call `requests` directly. Everything goes through
`GatewayClient` so that four properties hold for every provider without each
adapter having to remember them:

1. A timeout is always set. A gateway that stops answering must not hold a
   Gunicorn worker — and therefore a customer's checkout — open indefinitely.
2. Failures raise one exception type, so `services.py` can map "the gateway is
   unreachable" to a 502 without knowing which gateway.
3. Nothing sensitive is logged. Request bodies carry API keys and signatures;
   responses carry card metadata. Both pass through `redact` before they reach
   a log record or a stored `raw_response`.
4. Responses are size-limited. A provider is not a trusted source of unbounded
   JSON, and a 40 MB error page should not become a row in our database.
"""
import json
import logging

import requests
from requests import exceptions as requests_exceptions

from apps.core.logging_filters import redact

logger = logging.getLogger(__name__)

#: Largest provider response we will parse. Real payloads are a few kilobytes;
#: anything past this is an error page or an attack, not a payment.
MAX_RESPONSE_BYTES = 512 * 1024


class GatewayError(Exception):
    """
    The gateway could not be reached, or answered in a way we cannot use.

    Carries a customer-safe `message` and, separately, `detail` for the log.
    The distinction matters: a provider's error text can quote our own request
    back at us, credentials included.
    """

    def __init__(self, message: str, *, detail: str = "", status_code: int = 0, payload=None):
        super().__init__(message)
        self.message = message
        self.detail = detail or message
        self.status_code = status_code
        self.payload = payload if payload is not None else {}


class GatewayClient:
    """A configured, logged, timeout-bounded HTTP client for one gateway."""

    def __init__(self, *, provider_code: str, base_url: str, timeout: int = 20):
        self.provider_code = provider_code
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    # -- internals ---------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _parse(self, response) -> dict:
        content = response.content or b""
        if len(content) > MAX_RESPONSE_BYTES:
            raise GatewayError(
                "The payment provider sent an unexpectedly large response.",
                detail=f"{self.provider_code}: response exceeded {MAX_RESPONSE_BYTES} bytes",
                status_code=response.status_code,
            )
        if not content.strip():
            return {}
        try:
            parsed = json.loads(content.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise GatewayError(
                "The payment provider sent a response we could not read.",
                detail=f"{self.provider_code}: non-JSON response ({exc})",
                status_code=response.status_code,
            ) from exc
        # A provider is free to return a bare list or string; callers expect a
        # mapping, so wrap anything else rather than crashing on .get().
        if not isinstance(parsed, dict):
            return {"data": parsed}
        return parsed

    # -- public ------------------------------------------------------------

    def request(self, method: str, path: str, *, json_body=None, params=None, headers=None) -> dict:
        url = self._url(path)
        try:
            response = requests.request(
                method.upper(),
                url,
                json=json_body,
                params=params,
                headers={"Accept": "application/json", **(headers or {})},
                timeout=self.timeout,
            )
        except requests_exceptions.Timeout as exc:
            # Deliberately distinct wording: a timeout means the request may
            # well have been *received*, so the caller must reconcile rather
            # than assume nothing happened.
            raise GatewayError(
                "The payment provider did not respond in time.",
                detail=f"{self.provider_code}: timeout calling {method.upper()} {url}",
            ) from exc
        except requests_exceptions.RequestException as exc:
            raise GatewayError(
                "The payment provider could not be reached.",
                detail=f"{self.provider_code}: {type(exc).__name__} calling {method.upper()} {url}",
            ) from exc

        payload = self._parse(response)

        if response.status_code >= 400:
            logger.warning(
                "%s gateway returned %s for %s %s: %s",
                self.provider_code,
                response.status_code,
                method.upper(),
                url,
                json.dumps(redact(payload))[:1000],
            )
            raise GatewayError(
                "The payment provider rejected the request.",
                detail=f"{self.provider_code}: HTTP {response.status_code}",
                status_code=response.status_code,
                payload=redact(payload),
            )

        logger.info("%s gateway %s %s -> %s", self.provider_code, method.upper(), url, response.status_code)
        return payload

    def post(self, path: str, *, json_body=None, headers=None, params=None) -> dict:
        return self.request("POST", path, json_body=json_body, headers=headers, params=params)

    def get(self, path: str, *, params=None, headers=None) -> dict:
        return self.request("GET", path, params=params, headers=headers)
