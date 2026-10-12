"""
Sign in with Google or Facebook.

The browser never tells us who the customer is; it hands over a token, and we
ask Google or Facebook ourselves what that token proves:

  Google    an ID token (from Google Identity Services). Google's tokeninfo
            endpoint checks the signature and expiry; we check it was issued
            for *our* client id, by Google, for a verified email.
  Facebook  a user access token (from the JS SDK). debug_token, called with our
            app secret, proves the token belongs to *our* app and is live; /me
            then gives the id and email, with appsecret_proof so a token stolen
            from another site cannot be replayed here.

Then the identity is matched to a store account:

  1. a SocialAccount for this provider + provider user id → that account;
  2. else an account with the same email → linked to it, but only when the
     provider vouches for the email (otherwise anyone could claim an address
     and walk into its orders);
  3. else a new account: no usable password (the customer can set one through
     password reset), email already verified by the provider.
"""

import hashlib
import hmac

import requests
from django.conf import settings
from django.db import IntegrityError, transaction

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.social_auth.models import SocialAccount, SocialProvider
from apps.users.models import User

TIMEOUT = 10
GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def enabled_providers():
    """What the storefront may show: only providers configured on this server."""
    providers = {}
    if settings.GOOGLE_OAUTH_CLIENT_ID:
        providers["google"] = {"client_id": settings.GOOGLE_OAUTH_CLIENT_ID}
    if settings.FACEBOOK_APP_ID and settings.FACEBOOK_APP_SECRET:
        providers["facebook"] = {"app_id": settings.FACEBOOK_APP_ID, "graph_version": settings.FACEBOOK_GRAPH_VERSION}
    return providers


def _get(url, params):
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ApplicationError(
            ErrorCodes.AUTH_SOCIAL_UNAVAILABLE, "Could not reach the sign-in provider. Try again."
        ) from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def _invalid(message="The sign-in could not be verified. Try again."):
    return ApplicationError(ErrorCodes.AUTH_SOCIAL_TOKEN_INVALID, message)


def verify_google(credential):
    """A Google ID token → (uid, email, email_verified, first_name, last_name)."""
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        raise ApplicationError(ErrorCodes.AUTH_SOCIAL_PROVIDER_DISABLED, "Google sign-in is not enabled.")
    if not credential:
        raise _invalid()
    status, info = _get(GOOGLE_TOKENINFO, {"id_token": credential})
    if status != 200:
        raise _invalid()
    if info.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID or info.get("iss") not in GOOGLE_ISSUERS:
        raise _invalid()
    if not info.get("sub"):
        raise _invalid()
    verified = str(info.get("email_verified", "")).lower() == "true"
    return info["sub"], (info.get("email") or "").strip(), verified, info.get("given_name", ""), info.get("family_name", "")


def verify_facebook(access_token):
    """A Facebook user access token → (uid, email, email_verified, first_name, last_name)."""
    app_id, secret = settings.FACEBOOK_APP_ID, settings.FACEBOOK_APP_SECRET
    if not (app_id and secret):
        raise ApplicationError(ErrorCodes.AUTH_SOCIAL_PROVIDER_DISABLED, "Facebook sign-in is not enabled.")
    if not access_token:
        raise _invalid()
    graph = f"https://graph.facebook.com/{settings.FACEBOOK_GRAPH_VERSION}"

    status, debug = _get(f"{graph}/debug_token", {"input_token": access_token, "access_token": f"{app_id}|{secret}"})
    data = debug.get("data") or {}
    if status != 200 or not data.get("is_valid") or str(data.get("app_id")) != str(app_id):
        raise _invalid()

    proof = hmac.new(secret.encode(), access_token.encode(), hashlib.sha256).hexdigest()
    status, me = _get(
        f"{graph}/me",
        {"fields": "id,email,first_name,last_name", "access_token": access_token, "appsecret_proof": proof},
    )
    if status != 200 or not me.get("id") or str(me["id"]) != str(data.get("user_id")):
        raise _invalid()
    # Facebook only returns an address the person has confirmed with it.
    email = (me.get("email") or "").strip()
    return str(me["id"]), email, bool(email), me.get("first_name", ""), me.get("last_name", "")


@transaction.atomic
def resolve_user(provider, uid, email, email_verified, first_name="", last_name=""):
    """The store account for this identity, linking or creating it. Returns (user, created)."""
    link = SocialAccount.objects.select_related("user").filter(provider=provider, uid=uid).first()
    if link:
        user = link.user
    else:
        if not email:
            raise ApplicationError(
                ErrorCodes.AUTH_SOCIAL_EMAIL_REQUIRED,
                "Your account did not share an email address, which the store needs for your orders.",
            )
        if not email_verified:
            raise _invalid("This email address is not verified with the provider.")
        user = User.objects.filter(email__iexact=email).first()
        created = user is None
        if created:
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        email=email, password=None, first_name=first_name[:150], last_name=last_name[:150],
                        email_verified=True,
                    )
            except IntegrityError:  # the same person, signing in twice at once
                user = User.objects.get(email__iexact=email)
                created = False
        elif not user.email_verified:
            # The provider just proved this person owns the address.
            user.email_verified = True
            user.save(update_fields=["email_verified"])
        SocialAccount.objects.get_or_create(provider=provider, uid=uid, defaults={"user": user, "email": email})
        if not user.is_active:
            raise ApplicationError(ErrorCodes.AUTH_ACCOUNT_DISABLED, "This account has been disabled.")
        return user, created

    if not user.is_active:
        raise ApplicationError(ErrorCodes.AUTH_ACCOUNT_DISABLED, "This account has been disabled.")
    return user, False


VERIFIERS = {SocialProvider.GOOGLE: verify_google, SocialProvider.FACEBOOK: verify_facebook}
