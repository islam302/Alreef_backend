"""
The absolute webhook URL each provider must be told to call.

Built from `BACKEND_PUBLIC_URL`, never from the incoming request. A provider
calls this URL out of band, hours later, from its own infrastructure — so
deriving it from `request.get_host()` would mean an attacker who can set a Host
header gets to choose where our payment notifications are sent. That is the same
class of bug as the password-reset host in SECURITY.md, and it is worse here,
because the notification is what decides whether an order is paid.
"""
from django.conf import settings

#: Routes that actually send us webhooks — which is Paymob and nothing else.
#:
#: Cash on delivery and InstaPay are settled by a person, so a POST to a webhook
#: URL for either could only be hostile. Keeping them out of this tuple is what
#: makes those URLs 404 rather than relying on an adapter returning False.
WEBHOOK_PROVIDERS = ("paymob",)


def webhook_url(provider_code: str) -> str:
    base = str(getattr(settings, "BACKEND_PUBLIC_URL", "") or "").rstrip("/")
    return f"{base}/api/v1/payments/webhooks/{provider_code}/"


def all_webhook_urls() -> dict:
    """For the deployment checklist and `manage.py check_payments`."""
    return {code: webhook_url(code) for code in WEBHOOK_PROVIDERS}
