"""
The welcome offer: "create an account and order today, N% off".

Two steps, and the browser holds nothing it can change:

1. **Issue** (anonymous). The server picks the percentage and signs it,
   together with the moment it was issued, into an opaque token. The browser
   keeps that token on the device. Editing it — to 90, to a later issue time —
   breaks the signature, and the token is refused.

2. **Claim** (signed in). The token is checked and turned into a personal
   Coupon: owned by that account (nobody else can apply it, see
   services.check_owner), one use, and expiring 24 hours after the offer was
   first shown — not after the claim, so signing in late does not restart the
   clock.

An account gets one welcome coupon, ever, and only before its first order.
"""

import secrets
import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.coupons.models import Coupon, CouponDiscountType

SALT = "apps.coupons.welcome-offer"
CODE_PREFIX = "WELCOME-"
# No 0/O/1/I: the code may be read aloud or typed from a screenshot.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _percentages() -> list[int]:
    allowed = [int(p) for p in getattr(settings, "WELCOME_OFFER_PERCENTAGES", (5, 10)) if 0 < int(p) <= 50]
    return allowed or [5]


def _lifetime() -> timedelta:
    return timedelta(hours=int(getattr(settings, "WELCOME_OFFER_HOURS", 24)))


def _expires_at(issued_at: int) -> datetime:
    return datetime.fromtimestamp(issued_at, tz=dt_timezone.utc) + _lifetime()


def issue_token() -> dict:
    percentage = secrets.choice(_percentages())
    issued_at = int(time.time())
    token = signing.dumps({"p": percentage, "t": issued_at, "n": secrets.token_hex(6)}, salt=SALT, compress=True)
    return {"token": token, "percentage": percentage, "expires_at": _expires_at(issued_at)}


def read_token(token: str) -> dict:
    """Verify a token and return its terms. Raises on a forged or expired one."""
    try:
        payload = signing.loads(token or "", salt=SALT)
        percentage, issued_at = int(payload["p"]), int(payload["t"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ApplicationError(ErrorCodes.WELCOME_OFFER_INVALID, "This offer is not valid.")
    # The signature proves the server wrote these numbers; this proves the
    # server still offers them (the setting may have changed since).
    if percentage not in _percentages():
        raise ApplicationError(ErrorCodes.WELCOME_OFFER_INVALID, "This offer is not valid.")
    expires_at = _expires_at(issued_at)
    if expires_at <= timezone.now():
        raise ApplicationError(ErrorCodes.WELCOME_OFFER_EXPIRED, "This offer has expired.")
    return {"token": token, "percentage": percentage, "expires_at": expires_at}


def current_coupon(user) -> Coupon | None:
    """The account's welcome coupon while it is still spendable."""
    coupon = (
        Coupon.objects.filter(owner=user, code__startswith=CODE_PREFIX, is_active=True)
        .order_by("-created_at")
        .first()
    )
    if not coupon:
        return None
    if coupon.expiration_date and coupon.expiration_date <= timezone.now():
        return None
    if coupon.usages.exists():
        return None
    return coupon


def _has_ordered(user) -> bool:
    from apps.orders.models import Order, OrderStatus

    return Order.objects.filter(user=user).exclude(status=OrderStatus.CANCELLED).exists()


@transaction.atomic
def claim(*, user, token: str) -> Coupon:
    terms = read_token(token)

    # Serialise concurrent claims for one account, so two tabs cannot mint two.
    get_user_model().objects.select_for_update().filter(pk=user.pk).first()

    existing = Coupon.objects.filter(owner=user, code__startswith=CODE_PREFIX).order_by("-created_at").first()
    if existing:
        if current_coupon(user):
            return existing  # Claiming twice is harmless: same coupon back.
        raise ApplicationError(ErrorCodes.WELCOME_OFFER_NOT_ELIGIBLE, "This account has already had its welcome offer.")
    if _has_ordered(user):
        raise ApplicationError(ErrorCodes.WELCOME_OFFER_NOT_ELIGIBLE, "The welcome offer is for a first order.")

    while True:
        code = CODE_PREFIX + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        if not Coupon.objects.filter(code__iexact=code).exists():
            break

    return Coupon.objects.create(
        code=code,
        description=f"Welcome offer {terms['percentage']}%",
        discount_type=CouponDiscountType.PERCENTAGE,
        percentage_value=terms["percentage"],
        expiration_date=terms["expires_at"],
        usage_limit=1,
        per_user_limit=1,
        owner=user,
        is_active=True,
    )
