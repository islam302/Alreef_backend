"""
How somebody without an account gets back to their order.

A guest order has no user, so the usual "filter by request.user" that keeps one
customer out of another's orders has nothing to filter on. Two keys replace it,
and both are designed so that knowing one order does not help with the next:

* **The access token**, returned once by checkout. It is signed with
  SECRET_KEY (django.core.signing), so it cannot be made up or edited — changing
  the order id inside it breaks the signature — and it names one guest order
  only. It is what the confirmation page and the tracking page hold on to.

* **The order number plus the phone it was placed with.** Order numbers are
  sequential and printed on parcels, so the number alone proves nothing; the
  phone is the secret half. The comparison is constant-time, a wrong phone and
  an unknown number answer identically, and failures are capped per order
  number as well as per IP (the view's throttle), so the phone cannot be
  guessed one digit-run at a time from many addresses.

Orders that belong to an account are never reachable through either: their
owner signs in. Opening them up by phone would turn every account holder's
order history into something anyone holding their number could read.
"""
import hmac
import logging
from datetime import timedelta

import phonenumbers
from django.conf import settings
from django.core import signing
from django.core.cache import cache

from apps.core.frontend_links import frontend_url

logger = logging.getLogger(__name__)

TOKEN_SALT = "orders.guest-access.v1"
# Long enough to cover delivery, a return window and a slow refund; short
# enough that a token found in an old browser history eventually stops working.
TOKEN_MAX_AGE = timedelta(days=180)

# Wrong phones allowed against one order number before it stops answering, for
# an hour. Per order number, on top of the per-IP throttle on the view, because
# an attacker with many addresses is exactly who the IP limit does not stop.
LOOKUP_FAILURES_ALLOWED = 10
LOOKUP_LOCK_SECONDS = 60 * 60

# Compared against when the order number does not exist, so a miss costs the
# same as a wrong phone and the timing says nothing about which it was.
_NO_SUCH_PHONE = "+000000000000"


class LookupLocked(Exception):
    """Too many wrong phones for this order number; try again later."""


def issue_access_token(order) -> str:
    return signing.dumps({"o": order.pk, "n": order.order_number}, salt=TOKEN_SALT)


def order_for_token(token: str):
    """The guest order this token names, or None. Never raises on bad input."""
    from apps.orders.models import Order

    if not token or not isinstance(token, str) or len(token) > 512:
        return None
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:  # Also covers SignatureExpired.
        return None
    if not isinstance(payload, dict):
        return None
    return Order.objects.filter(
        pk=payload.get("o"), order_number=payload.get("n"), user__isnull=True
    ).first()


def normalize_phone(raw: str) -> str:
    """E.164, the form orders store; empty if it is not a valid number."""
    try:
        parsed = phonenumbers.parse(str(raw or "").strip(), settings.PHONENUMBER_DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(parsed):
        return ""
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _failure_key(order_number: str) -> str:
    return f"guest-order-lookup-failures:{order_number}"


def order_for_number_and_phone(order_number: str, phone: str):
    """
    The guest order with this number, if `phone` is the one it was placed with.

    Raises LookupLocked once the number has seen too many wrong phones. The
    counter is kept for numbers that do not exist too, so a lock is not itself
    a signal that an order is real.
    """
    from apps.orders.models import Order

    number = str(order_number or "").strip().upper()[:30]
    key = _failure_key(number)
    if cache.get(key, 0) >= LOOKUP_FAILURES_ALLOWED:
        raise LookupLocked()

    order = Order.objects.filter(order_number=number, user__isnull=True).first() if number else None
    expected = order.customer_phone if order else _NO_SUCH_PHONE
    supplied = normalize_phone(phone)
    matches = hmac.compare_digest(expected.encode(), (supplied or "-").encode())

    if order is not None and supplied and matches:
        return order

    if cache.add(key, 1, timeout=LOOKUP_LOCK_SECONDS) is False:
        try:
            cache.incr(key)
        except ValueError:  # Expired between add and incr.
            cache.set(key, 1, timeout=LOOKUP_LOCK_SECONDS)
    return None


def order_url_for(order) -> str:
    """Where an email's "see your order" points for this order's customer."""
    if order.user_id:
        return frontend_url(f"/account/orders/{order.order_number}")
    return frontend_url("/track", order=order.order_number)
