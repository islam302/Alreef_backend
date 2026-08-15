import json
import logging
import re
import time

SENSITIVE_KEYS = re.compile(
    r"(password|token|secret|authorization|api[_-]?key|cvv|card[_-]?number|hmac|access[_-]?token|refresh[_-]?token)",
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
