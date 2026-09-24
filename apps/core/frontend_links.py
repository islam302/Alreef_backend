"""
Building links that go out in email and SMS.

THE RULE: THE HOST COMES FROM SETTINGS, NEVER FROM THE REQUEST.

A link in an outbound message is sent by us, over our domain, to an address
we looked up ourselves — so the recipient trusts it, and it usually carries a
single-use token that is as good as a password. If any part of the host can
be chosen by whoever called the endpoint, the attacker writes the link and we
deliver it:

    POST /api/v1/auth/password-reset-request/
    {"email": "victim@example.com", "frontend_reset_url": "https://evil.example/x"}

The victim gets a genuine, correctly-signed message from us containing a real
reset token pointed at the attacker's server. That is account takeover with no
phishing infrastructure and no spoofing — we do the delivery.

So callers pass a path, and only a path. FRONTEND_URL decides the origin.
"""
from urllib.parse import urlencode, urlsplit, urlunsplit

from django.conf import settings


def frontend_url(path: str = "/", **query) -> str:
    """
    An absolute URL on the configured frontend origin.

    `path` is treated as a path, never as a URL: anything that arrives with a
    scheme, an authority, or a protocol-relative "//" prefix is reduced to its
    path so a caller-supplied value can never move the link to another host.
    """
    parts = urlsplit(str(path or "/"))
    # Drop scheme and netloc outright — a caller does not get to pick the host.
    safe_path = parts.path or "/"
    while safe_path.startswith("//"):
        safe_path = safe_path[1:]
    if not safe_path.startswith("/"):
        safe_path = "/" + safe_path

    base = urlsplit(settings.FRONTEND_URL.rstrip("/"))
    merged_query = urlencode({k: v for k, v in query.items() if v is not None})

    return urlunsplit((base.scheme, base.netloc, base.path + safe_path, merged_query, ""))
