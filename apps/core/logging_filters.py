import json
import logging
import re
import time

# Anything whose key matches this is never logged and never stored in a
# PaymentTransaction.raw_response. The payment-provider entries are as
# important as the auth ones: a provider `signature` or a Paymob `hmac` in our
# database is a forgery oracle, and `pan`/`source_data` can carry card data
# even when the provider believes it masked them.
SENSITIVE_KEYS = re.compile(
    r"(password|token|secret|authorization|api[_-]?key|cvv|cvc|card[_-]?number"
    r"|hmac|access[_-]?token|refresh[_-]?token"
    r"|signature|secure[_-]?key|pan|source_data|iban|account[_-]?number)",
    re.IGNORECASE,
)
REDACTED = "***REDACTED***"


def redact(value):
    if isinstance(value, dict):
        return {
            k: (REDACTED if SENSITIVE_KEYS.search(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class SensitiveDataFilter(logging.Filter):
    """Prevents passwords / tokens / card data / secrets from ever reaching logs."""

    def filter(self, record):
        if isinstance(record.args, dict):
            record.args = redact(record.args)
        if isinstance(record.msg, str) and SENSITIVE_KEYS.search(record.msg):
            record.msg = SENSITIVE_KEYS.sub(REDACTED, record.msg)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        return json.dumps(payload)
