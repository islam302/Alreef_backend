"""
Security regressions that are not about *who owns which row* — that is
tests/test_security_authz.py. This file covers the rest:

  * links we put in outbound mail, and who gets to choose where they point
  * account-recovery flows: enumeration, token reuse, token expiry
  * session teardown: a blacklisted refresh token must stay dead
  * what the API is allowed to say about a user (never a password hash)
  * upload validation, fail-closed webhooks, throttle scopes
  * the settings that only matter in production, asserted against the
    production module rather than trusted to a checklist

Most of these guard a one-line mistake that no functional test would notice,
because the feature keeps working perfectly while being insecure.
"""
import importlib
from unittest import mock

import pytest
from django.urls import resolve

from apps.brands.models import Brand
from apps.catalog.models import ProductVideo
from apps.categories.models import Category
from apps.core.frontend_links import frontend_url
from apps.core.validators import validate_image_file
from apps.shipping.providers.base import ShippingProvider
from apps.users.models import User

pytestmark = pytest.mark.django_db


# ===========================================================================
# Outbound links — the host is ours, whatever the caller says
# ===========================================================================

RESET_URL = "/api/v1/auth/password-reset/"


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/collect",
        "//evil.example/collect",
        "http://evil.example",
        "https://alreef.com.evil.example/reset",
        "javascript:alert(1)",
    ],
)
def test_password_reset_link_ignores_a_caller_supplied_host(api_client, user, settings, hostile):
    """
    The attack this closes: anyone can ask us to mail a victim a real,
    single-use reset token pointed at a server they control. The mail is
    genuinely from us, correctly signed, and lands in the victim's inbox — we
    do the phishing on the attacker's behalf, and one click is account
    takeover.

    So whatever key the request uses to smuggle a URL in, the link that goes
    out has to sit on FRONTEND_URL.
    """
    settings.FRONTEND_URL = "https://shop.alreef.test"

    with mock.patch("apps.users.views.send_email_task") as task:
        response = api_client.post(
            RESET_URL,
            {
                "email": user.email,
                "frontend_reset_url": hostile,
                "reset_url": hostile,
                "next": hostile,
            },
            format="json",
        )

    assert response.status_code == 200
    assert task.delay.called, "no reset mail was queued for a known address"
    sent_url = task.delay.call_args.kwargs["context"]["reset_url"]

    assert sent_url.startswith("https://shop.alreef.test/"), sent_url
    assert "evil.example" not in sent_url
    assert "javascript:" not in sent_url


def test_email_verification_link_is_absolute_and_on_our_own_origin(
    authenticated_client, settings
):
    """The old link was a bare "?token=..." — not a link at all in an email
    client, and the fix must not "solve" that by trusting the request."""
    settings.FRONTEND_URL = "https://shop.alreef.test"

    with mock.patch("apps.users.views.send_email_task") as task:
        response = authenticated_client.post(
            "/api/v1/auth/verify-email/", {"frontend_url": "https://evil.example"}, format="json"
        )

    assert response.status_code == 200
    verify_url = task.delay.call_args.kwargs["context"]["verify_url"]
    assert verify_url.startswith("https://shop.alreef.test/")
    assert "evil.example" not in verify_url


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/x",
        "//evil.example/x",
        "http://user:pass@evil.example/x",
        "/../../etc/passwd",
        "",
        None,
    ],
)
def test_frontend_url_helper_cannot_be_moved_off_the_configured_origin(settings, path):
    settings.FRONTEND_URL = "https://shop.alreef.test"
    built = frontend_url(path, token="abc")
    assert built.startswith("https://shop.alreef.test/"), built
    assert "evil.example" not in built


# ===========================================================================
# Account recovery
# ===========================================================================


def test_password_reset_does_not_reveal_whether_an_email_is_registered(api_client, user):
    """Same status, same message, both ways — otherwise the endpoint is a
    free membership oracle for a credential-stuffing list."""
    known = api_client.post(RESET_URL, {"email": user.email}, format="json")
    unknown = api_client.post(RESET_URL, {"email": "nobody@example.com"}, format="json")

    assert known.status_code == unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]


def test_a_password_reset_token_works_once(api_client, user):
    from apps.users.services import issue_password_reset_token

    token = issue_password_reset_token(user)
    body = {"token": token, "new_password": "BrandNewSecret123"}

    first = api_client.post("/api/v1/auth/password-reset-confirm/", body, format="json")
    assert first.status_code == 200, first.content

    second = api_client.post(
        "/api/v1/auth/password-reset-confirm/",
        {"token": token, "new_password": "YetAnotherSecret123"},
        format="json",
    )
    assert second.status_code >= 400

    user.refresh_from_db()
    assert user.check_password("BrandNewSecret123")


def test_an_expired_password_reset_token_is_refused(api_client, user):
    from datetime import timedelta

    from django.utils import timezone

    from apps.users.models import PasswordResetToken
    from apps.users.services import issue_password_reset_token

    token = issue_password_reset_token(user)
    expired_at = timezone.now() - timedelta(minutes=1)
    assert PasswordResetToken.objects.filter(token=token).update(expires_at=expired_at) == 1

    response = api_client.post(
        "/api/v1/auth/password-reset-confirm/",
        {"token": token, "new_password": "BrandNewSecret123"},
        format="json",
    )
    assert response.status_code >= 400
    user.refresh_from_db()
    assert not user.check_password("BrandNewSecret123")


def test_phone_otp_stops_accepting_guesses_after_the_attempt_limit(authenticated_client, user):
    """A six-digit code is only safe while the number of guesses is capped."""
    from apps.users.models import PhoneOTP
    from apps.users.services import OTP_MAX_ATTEMPTS, issue_phone_otp

    real_code = issue_phone_otp(user)
    wrong = "000000" if real_code != "000000" else "111111"

    for _ in range(OTP_MAX_ATTEMPTS):
        authenticated_client.post("/api/v1/auth/verify-phone/confirm/", {"code": wrong}, format="json")

    # Even the correct code is refused once the record is burned.
    response = authenticated_client.post(
        "/api/v1/auth/verify-phone/confirm/", {"code": real_code}, format="json"
    )
    assert response.status_code >= 400
    user.refresh_from_db()
    assert user.phone_verified is False
    assert PhoneOTP.objects.filter(user=user).first().attempts >= OTP_MAX_ATTEMPTS


# ===========================================================================
# Session teardown
# ===========================================================================


def test_a_logged_out_refresh_token_cannot_mint_another_access_token(api_client, user):
    login = api_client.post(
        "/api/v1/auth/login/", {"email": user.email, "password": "SuperSecret123"}, format="json"
    )
    assert login.status_code == 200, login.content
    tokens = login.json()["data"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    logout = api_client.post("/api/v1/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
    assert logout.status_code == 200, logout.content

    api_client.credentials()
    replay = api_client.post(
        "/api/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json"
    )
    assert replay.status_code >= 400, "a blacklisted refresh token still works"


def test_rotating_a_refresh_token_retires_the_old_one(api_client, user):
    """ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION only help if they are
    actually on — a stolen refresh token should die the moment the real
    browser refreshes."""
    login = api_client.post(
        "/api/v1/auth/login/", {"email": user.email, "password": "SuperSecret123"}, format="json"
    )
    original = login.json()["data"]["refresh"]

    rotated = api_client.post("/api/v1/auth/token/refresh/", {"refresh": original}, format="json")
    assert rotated.status_code == 200, rotated.content
    assert rotated.json()["data"].get("refresh"), "rotation is not returning a new refresh token"

    replay = api_client.post("/api/v1/auth/token/refresh/", {"refresh": original}, format="json")
    assert replay.status_code >= 400, "the superseded refresh token still works"


def test_a_disabled_account_cannot_log_in(api_client, user):
    user.is_active = False
    user.save(update_fields=["is_active"])

    response = api_client.post(
        "/api/v1/auth/login/", {"email": user.email, "password": "SuperSecret123"}, format="json"
    )
    assert response.status_code >= 400


# ===========================================================================
# What the API is allowed to say
# ===========================================================================


SECRETISH = ("password", "code_hash", "secret", "hmac", "api_key", "private")


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _flatten(inner, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for item in value:
            yield from _flatten(item, prefix)
    else:
        yield prefix, value


@pytest.mark.parametrize(
    "path",
    ["/api/v1/auth/me/", "/api/v1/orders/", "/api/v1/addresses/", "/api/v1/notifications/"],
)
def test_customer_endpoints_never_serialise_a_credential(authenticated_client, address, path):
    response = authenticated_client.get(path)
    assert response.status_code == 200, response.content

    for key, value in _flatten(response.json()):
        leaf = key.split(".")[-1].lower()
        assert not any(s in leaf for s in SECRETISH), f"{path} exposes {key}"
        if isinstance(value, str):
            assert not value.startswith(("pbkdf2_", "argon2", "bcrypt", "md5$")), (
                f"{path} exposes a password hash at {key}"
            )


def test_admin_customer_detail_never_serialises_a_password_hash(staff_client, user, address):
    response = staff_client.get(f"/api/v1/admin-api/customers/{user.id}/")
    if response.status_code == 404:  # pragma: no cover - route shape guard
        pytest.skip("customer detail is not keyed by id")
    assert response.status_code == 200, response.content

    for key, value in _flatten(response.json()):
        assert "password" not in key.lower(), f"admin customer detail exposes {key}"
        if isinstance(value, str):
            assert not value.startswith(("pbkdf2_", "argon2", "bcrypt", "md5$"))


def test_an_unhandled_error_does_not_leak_a_traceback(api_client, settings):
    """DEBUG=True in production turns every 500 into a source listing with
    settings values in the local variables. testing.py already sets it False;
    this asserts the envelope stays opaque."""
    assert settings.DEBUG is False

    response = api_client.get("/api/v1/orders/does-not-exist/")
    assert b"Traceback" not in response.content
    assert b"django.db" not in response.content


# ===========================================================================
# Uploads, webhooks, throttles
# ===========================================================================


@pytest.mark.parametrize(
    "model,field_name",
    [
        (Category, "image"),
        (Brand, "logo"),
        (User, "profile_image"),
        (ProductVideo, "thumbnail"),
    ],
)
def test_image_upload_fields_use_shared_validator(model, field_name):
    """Every ImageField that accepts user/staff-uploaded content must run
    through validate_image_file (extension + size + magic-byte + dimension
    checks) — not just Product.image and Review.image."""
    field = model._meta.get_field(field_name)
    assert validate_image_file in field.validators


class _MinimalShippingProvider(ShippingProvider):
    code = "test-carrier"

    def create_shipment(self, *, shipment, order):
        raise NotImplementedError

    def cancel_shipment(self, *, shipment):
        raise NotImplementedError

    def get_tracking(self, *, shipment):
        raise NotImplementedError


def test_shipping_provider_verify_webhook_defaults_fail_closed():
    """A concrete carrier adapter that forgets to override verify_webhook()
    must reject webhooks by default, not silently accept unsigned callbacks."""
    provider = _MinimalShippingProvider()
    assert provider.verify_webhook(headers={}, body=b"{}") is False


@pytest.mark.parametrize(
    "provider",
    # The one gateway that has a webhook, plus routes that do not and never
    # will. An unsigned POST to any of them must be a no-op.
    ["paymob", "cod", "instapay", "manual", "fawry", "valu"],
)
def test_unconfigured_payment_webhooks_change_nothing(api_client, provider):
    """
    An unsigned POST must never mark a payment paid.

    Covers three situations at once: a gateway that is switched off at our end,
    a route that is settled by a person and has no webhook at all, and a gateway
    that was removed from the system entirely. "We will verify it later" is how a
    webhook becomes free money, so the answer in every case is that nothing moves.
    """
    from apps.payments.models import Payment

    before = list(Payment.objects.values_list("id", "status"))
    response = api_client.post(
        f"/api/v1/payments/webhooks/{provider}/",
        {"success": True, "amount_cents": 100000, "order": 1},
        format="json",
    )
    assert response.status_code in {200, 202, 400, 403, 404}
    assert list(Payment.objects.values_list("id", "status")) == before


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/v1/auth/logout/", {"auth", "auth_confirm"}),
        ("/api/v1/auth/token/refresh/", {"auth", "auth_confirm"}),
        ("/api/v1/auth/change-password/", {"auth", "auth_confirm"}),
        ("/api/v1/auth/password-reset/", {"auth", "auth_confirm"}),
        ("/api/v1/auth/password-reset-confirm/", {"auth", "auth_confirm"}),
        ("/api/v1/auth/verify-email/", {"auth", "auth_confirm"}),
        ("/api/v1/auth/verify-email/confirm/", {"auth", "auth_confirm"}),
        ("/api/v1/auth/verify-phone/confirm/", {"auth", "auth_confirm"}),
        # Endpoints that answer "is this code real?" about something worth
        # money. The codes are high-entropy, but the oracle should still not
        # be available at the generic 300/min authenticated rate.
        ("/api/v1/gift-cards/check/", {"gift_card"}),
        ("/api/v1/cart/coupon/", {"coupon"}),
        # Unauthenticated writes.
        ("/api/v1/admin-api/events/track/", {"analytics"}),
        ("/api/v1/content/contact-messages/", {"contact"}),
    ],
)
def test_abuse_prone_endpoints_carry_an_explicit_throttle_scope(path, expected):
    """Resolving the view and reading the attribute, rather than burning
    hundreds of requests guessing where the limit is."""
    view_class = resolve(path).func.view_class
    assert getattr(view_class, "throttle_scope", None) in expected


def test_every_declared_throttle_scope_has_a_configured_rate(settings):
    """A scope with no matching rate is not a slow limit — it is no limit,
    silently."""
    from django.urls import URLPattern, URLResolver, get_resolver

    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    missing = set()

    def walk(resolver):
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern)
            elif isinstance(pattern, URLPattern):
                view = getattr(pattern.callback, "cls", None) or getattr(
                    pattern.callback, "view_class", None
                )
                scope = getattr(view, "throttle_scope", None)
                if scope and scope not in rates:
                    missing.add(scope)

    walk(get_resolver())
    assert not missing, f"throttle scopes with no rate: {sorted(missing)}"


def test_login_is_rate_limited(api_client, user, settings):
    """Password guessing has to hit a wall well before it hits the database
    a thousand times."""
    limit = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth"].split("/")[0])

    statuses = [
        api_client.post(
            "/api/v1/auth/login/", {"email": user.email, "password": "wrong"}, format="json"
        ).status_code
        for _ in range(limit + 3)
    ]
    assert 429 in statuses, f"no 429 within {limit + 3} failed logins: {statuses}"


# ===========================================================================
# Configuration — asserted against the real production module
# ===========================================================================


def test_default_drf_permission_is_not_allow_any(settings):
    """A view that forgets permission_classes inherits this. It must fail
    towards 'read-only for strangers', never 'open'."""
    defaults = settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]
    assert "AllowAny" not in " ".join(defaults)


def test_every_admin_api_view_requires_staff():
    """One viewset added to the router without IsAdminUser would expose the
    whole back office, and it would look completely normal in review."""
    from django.urls import URLPattern, URLResolver, get_resolver

    offenders = []

    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern, prefix + str(pattern.pattern))
            elif isinstance(pattern, URLPattern):
                full = prefix + str(pattern.pattern)
                if "admin-api" not in full:
                    continue
                view = getattr(pattern.callback, "cls", None) or getattr(
                    pattern.callback, "view_class", None
                )
                if view is None:
                    continue
                names = {c.__name__ for c in getattr(view, "permission_classes", [])}
                # The analytics beacon is deliberately public; it is scoped
                # and writes nothing a stranger can read back.
                if view.__name__ == "TrackEventView":
                    continue
                if not names & {"IsAdminUser", "IsAdminOrReadOnly"}:
                    offenders.append(f"{full} -> {view.__module__}.{view.__name__} {names}")

    walk(get_resolver())
    assert not offenders, "admin routes without a staff check:\n" + "\n".join(sorted(set(offenders)))


def _reload_production(monkeypatch, tmp_path, **env):
    """Import config.settings.production against a controlled environment.

    Two things get in the way of just reloading it. `from .base import *`
    will not re-execute base.py while it sits in sys.modules, so SECRET_KEY
    would keep whatever value it was first read with. And python-decouple
    falls back to the repository's own .env, so a developer's local
    SECURE_SSL_REDIRECT=False would be read as if it were production's.
    Pointing decouple at an empty directory is what makes this test assert
    the code's defaults rather than one machine's settings.
    """
    import decouple

    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")

    # Production refuses to boot without these rather than silently inheriting
    # base.py's localhost defaults, so every caller needs them. Supplying them
    # here keeps each test asserting the one thing it is about; the guards
    # themselves are asserted by the tests below. Pass None to leave one unset
    # and watch the boot fail.
    required = {
        "CORS_ALLOWED_ORIGINS": "https://alreef.test",
        "CSRF_TRUSTED_ORIGINS": "https://alreef.test",
        "FRONTEND_URL": "https://alreef.test",
        "BACKEND_PUBLIC_URL": "https://api.alreef.test",
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_HOST": "smtp.alreef.test",
    }
    for key, value in {**required, **env}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    monkeypatch.setattr(decouple, "config", decouple.AutoConfig(str(tmp_path)))
    base = importlib.import_module("config.settings.base")
    monkeypatch.setattr(base, "config", decouple.config, raising=False)
    importlib.reload(base)

    production = importlib.import_module("config.settings.production")
    monkeypatch.setattr(production, "config", decouple.config, raising=False)
    return importlib.reload(production)


def test_production_settings_are_hardened(monkeypatch, tmp_path):
    """Read the production module for real rather than trusting the
    deployment checklist. Anything here that regresses is invisible in
    development, where none of it is switched on."""
    production = _reload_production(
        monkeypatch,
        tmp_path,
        SECRET_KEY="a-real-random-production-key-for-this-test",
        ALLOWED_HOSTS="alreef.test",
    )

    assert production.DEBUG is False
    assert production.SESSION_COOKIE_SECURE is True
    assert production.CSRF_COOKIE_SECURE is True
    assert production.SESSION_COOKIE_HTTPONLY is True
    assert production.SECURE_SSL_REDIRECT is True
    assert production.SECURE_HSTS_SECONDS >= 31536000
    assert production.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
    assert production.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert production.X_FRAME_OPTIONS == "DENY"
    assert "*" not in production.ALLOWED_HOSTS
    assert production.SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] is True
    assert production.SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"] is True
    assert production.SIMPLE_JWT["ALGORITHM"] == "HS256"
    assert production.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds() <= 60 * 60
    # Persistent connections must be probed before reuse, or a database restart
    # hands dead sockets to customers until every worker's pool cycles.
    assert production.DATABASES["default"]["CONN_HEALTH_CHECKS"] is True
    assert production.DATABASES["default"]["CONN_MAX_AGE"] > 0
    # Authentication is a Bearer header; cross-origin credentials buy nothing and
    # only widen what a mis-set origin list would expose.
    assert production.CORS_ALLOW_CREDENTIALS is False


@pytest.mark.parametrize(
    "missing", ["CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS", "FRONTEND_URL", "BACKEND_PUBLIC_URL"]
)
def test_production_refuses_to_boot_on_an_inherited_localhost_default(monkeypatch, tmp_path, missing):
    """
    Each of these falls back to localhost in base.py. That is right for a
    developer and wrong for a deployment: FRONTEND_URL builds the links inside
    password-reset and verification emails, so the default mailed customers a
    link to their own machine — and nothing failed, which is what made it
    dangerous.
    """
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured) as exc:
        _reload_production(
            monkeypatch, tmp_path,
            SECRET_KEY="a-real-random-production-key-for-this-test",
            ALLOWED_HOSTS="alreef.test",
            **{missing: None},
        )
    assert missing in str(exc.value)


def test_production_refuses_a_mail_backend_that_cannot_send(monkeypatch, tmp_path):
    """
    The console backend was reaching production, which broke account recovery
    outright and wrote single-use reset links into the container logs.
    """
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured) as exc:
        _reload_production(
            monkeypatch, tmp_path,
            SECRET_KEY="a-real-random-production-key-for-this-test",
            ALLOWED_HOSTS="alreef.test",
            EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        )
    assert "does not send mail" in str(exc.value)


def test_a_non_sending_backend_is_allowed_only_when_asked_for_by_name(monkeypatch, tmp_path):
    """CI and smoke stacks legitimately send nothing; they have to say so."""
    production = _reload_production(
        monkeypatch, tmp_path,
        SECRET_KEY="a-real-random-production-key-for-this-test",
        ALLOWED_HOSTS="alreef.test",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ALLOW_UNSENDABLE_EMAIL_BACKEND="True",
    )
    assert "locmem" in production.EMAIL_BACKEND


def test_production_refuses_smtp_without_a_host(monkeypatch, tmp_path):
    """Otherwise the failure lands on a customer at send time, not at boot."""
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured) as exc:
        _reload_production(
            monkeypatch, tmp_path,
            SECRET_KEY="a-real-random-production-key-for-this-test",
            ALLOWED_HOSTS="alreef.test",
            EMAIL_HOST=None,
        )
    assert "EMAIL_HOST" in str(exc.value)


def test_production_refuses_to_boot_on_the_placeholder_secret_key(monkeypatch, tmp_path):
    """The fallback key in base.py is public — it is in the repository. A
    deploy that forgets to set SECRET_KEY would sign every JWT with a key
    anyone can read, so the boot has to fail instead of succeeding quietly."""
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured):
        _reload_production(
            monkeypatch,
            tmp_path,
            SECRET_KEY="django-insecure-change-me-in-env",
            ALLOWED_HOSTS="alreef.test",
        )


def test_the_openapi_schema_is_not_published_by_default_in_production(monkeypatch, tmp_path):
    """The schema is a complete map of every route, admin included. It is
    worth having in development and on staging, so it stays a switch — but
    the switch has to default to off where DEBUG is off."""
    production = _reload_production(
        monkeypatch,
        tmp_path,
        SECRET_KEY="a-real-random-production-key-for-this-test",
        ALLOWED_HOSTS="alreef.test",
    )
    assert production.API_DOCS_ENABLED is False


def test_schema_routes_disappear_when_the_switch_is_off(api_client):
    """Flipping the setting has to actually unmount the routes, not merely
    hide a link to them.

    The URLconf is built once at import, so the module is reloaded under the
    overridden setting and put back afterwards — otherwise this test would
    depend on whether the developer's .env happens to have DEBUG on.
    """
    import config.urls
    from django.test import override_settings
    from django.urls import clear_url_caches

    try:
        with override_settings(API_DOCS_ENABLED=False):
            importlib.reload(config.urls)
            clear_url_caches()
            for path in ("/api/schema/", "/api/docs/", "/api/redoc/"):
                assert api_client.get(path).status_code == 404, path
            # The rest of the API is untouched.
            assert api_client.get("/api/v1/products/").status_code == 200
    finally:
        importlib.reload(config.urls)
        clear_url_caches()


def test_cors_is_not_open_to_every_origin(settings):
    """CORS_ALLOW_CREDENTIALS is on, so a wildcard origin would hand any site
    the ability to read authenticated responses."""
    assert getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False) is False
    assert "*" not in settings.CORS_ALLOWED_ORIGINS


def test_password_validators_are_configured(settings):
    names = {v["NAME"].rsplit(".", 1)[-1] for v in settings.AUTH_PASSWORD_VALIDATORS}
    assert {"MinimumLengthValidator", "CommonPasswordValidator", "NumericPasswordValidator"} <= names


def test_registration_rejects_a_weak_password(api_client):
    response = api_client.post(
        "/api/v1/auth/register/",
        {"email": "weak@example.com", "password": "12345678", "first_name": "A", "last_name": "B"},
        format="json",
    )
    assert response.status_code >= 400
    assert not User.objects.filter(email="weak@example.com").exists()
