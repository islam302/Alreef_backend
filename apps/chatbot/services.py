"""
The storefront's chat assistant, answered by the Zaychain RAG API.

The browser never sees the API key: it posts to /api/v1/chat/, and this module
forwards the question with `Authorization: ApiKey <key>`. Only the answer
comes back — the provider's reply also carries token counts, cost and the
account's points balance, none of which a shopper should be able to read.
"""

import logging

import requests
from django.conf import settings

from apps.core.error_codes import ApplicationError, ErrorCodes

logger = logging.getLogger(__name__)


def ask(question, history, language):
    """Forward one question with its recent history → the assistant's answer."""
    if not settings.CHATBOT_API_KEY:
        raise ApplicationError(ErrorCodes.CHAT_DISABLED, "The chat assistant is not enabled.")

    try:
        response = requests.post(
            settings.CHATBOT_API_URL,
            json={"question": question, "history": history, "language": language},
            headers={"Authorization": f"ApiKey {settings.CHATBOT_API_KEY}", "Accept": "application/json"},
            timeout=settings.CHATBOT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Chat assistant unreachable: %s", exc.__class__.__name__)
        raise _unavailable() from exc

    if response.status_code != 200:
        # Logged without the body: an upstream error page may echo the request.
        logger.warning("Chat assistant answered HTTP %s", response.status_code)
        raise _unavailable()

    try:
        answer = response.json().get("answer")
    except (ValueError, AttributeError):
        answer = None
    if not isinstance(answer, str) or not answer.strip():
        logger.warning("Chat assistant reply had no answer")
        raise _unavailable()
    return answer.strip()


def _unavailable():
    return ApplicationError(ErrorCodes.CHAT_UNAVAILABLE, "The chat assistant is not available right now. Try again.")
