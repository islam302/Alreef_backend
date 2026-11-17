"""
The sign-in, sign-up and password-reset matrix.

Every way a real Egyptian customer types who they are — an email the phone
keyboard capitalised, a stray space, a mobile number in any of the shapes it is
written in (local 01..., +20..., 20..., spaced, dashed, or in Arabic-Indic
digits off an Arabic keyboard) — crossed with the right and the wrong password,
through the real endpoints. Plus: registration refusing the same email or phone
in any spelling, the reset flow for known / unknown / malformed addresses, reset
tokens that work exactly once and never after expiry, social sign-ups with a
mixed-case email, and the per-account lockout.
"""
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.models import PasswordResetToken, User

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
REGISTER = "/api/v1/auth/register/"
RESET = "/api/v1/auth/password-reset/"
RESET_CONFIRM = "/api/v1/auth/password-reset-confirm/"
ME = "/api/v1/auth/me/"

EMAIL = "jane.doe@example.com"
PHONE_E164 = "+201012345678"
PASSWORD = "Login-Pass-2026!x"
WRONG = "Not-The-Pass-2026!"

# What a customer may type in the email box for EMAIL.
EMAIL_SPELLINGS = {
    "exact": EMAIL,
    "upper": EMAIL.upper(),
    "capitalised": EMAIL.capitalize(),
    "padded": f"  {EMAIL}  ",
    "padded_mixed": f"\t Jane.Doe@Example.COM ",
}

# Every way PHONE_E164 is written down in Egypt.
PHONE_SPELLINGS = {
    "local": "01012345678",
    "e164": "+201012345678",
    "intl_no_plus": "201012345678",
    "spaced_intl": "+20 101 234 5678",
    "spaced_local": "010 1234 5678",
    "dashed_local": "010-1234-5678",
    "arabic_indic_local": "٠١٠١٢٣٤٥٦٧٨",
    "arabic_indic_intl": "+٢٠١٠١٢٣٤٥٦٧٨",
    "persian_digits_local": "۰۱۰۱۲۳۴۵۶۷۸",
}


@pytest.fixture
def no_throttle():
    """Lockout tests need more attempts than the IP throttle lets through in a
    minute; the account lockout is what they are testing, not the throttle."""
    with mock.patch("rest_framework.views.APIView.check_throttles", return_value=None):
        yield


@pytest.fixture
def account(db):
    return User.objects.create_user(
        email=EMAIL, password=PASSWORD, phone=PHONE_E164, first_name="Jane", last_name="Doe",
    )


def _login(identifier, password, client=None):
    return (client or APIClient()).post(LOGIN, {"email": identifier, "password": password}, format="json")


def _code(response):
    return response.json()["errors"]["code"]


# ---------------------------------------------------------------------------
# Login: identifier spelling x password
# ---------------------------------------------------------------------------

IDENTIFIERS = [pytest.param(v, id=f"email-{k}") for k, v in EMAIL_SPELLINGS.items()] + [
    pytest.param(v, id=f"phone-{k}") for k, v in PHONE_SPELLINGS.items()
]


@pytest.mark.parametrize("identifier", IDENTIFIERS)
def test_login_with_right_password_signs_in_the_account(account, identifier):
    response = _login(identifier, PASSWORD)
    assert response.status_code == 200, (identifier, response.content)
    data = response.json()["data"]
    assert data["user"]["id"] == account.id
    assert data["access"] and data["refresh"]
    me = APIClient().get(ME, HTTP_AUTHORIZATION=f"Bearer {data['access']}")
    assert me.status_code == 200 and me.json()["data"]["email"] == EMAIL


@pytest.mark.parametrize("identifier", IDENTIFIERS)
def test_login_with_wrong_password_is_refused_the_same_way(account, identifier):
    response = _login(identifier, WRONG)
    assert response.status_code == 401, (identifier, response.content)
    assert _code(response) == "AUTH_INVALID_CREDENTIALS"
    assert "access" not in (response.json().get("data") or {})


@pytest.mark.parametrize(
    "identifier",
    [
        "someone.else@example.com",
        "01099999999",  # a valid number nobody registered
        "+201099999999",
        "0101234567",  # one digit short
        "jane.doe",  # no domain
        "",
        "   ",
    ],
)
def test_login_with_an_unknown_identifier_is_refused(account, identifier):
    response = _login(identifier, PASSWORD)
    assert response.status_code in (400, 401), (identifier, response.content)
    assert _code(response) in ("AUTH_INVALID_CREDENTIALS", "VALIDATION_ERROR")


@pytest.mark.parametrize("password", ["", PASSWORD.upper(), PASSWORD.lower(), PASSWORD[:-1], PASSWORD + "x"])
def test_the_password_itself_is_exact(account, password):
    """Only the identifier is forgiving. The password's case and characters count."""
    response = _login(EMAIL, password)
    assert response.status_code in (400, 401), (password, response.content)


@pytest.mark.parametrize("typed", [PASSWORD + " ", " " + PASSWORD, f"  {PASSWORD}	"])
def test_surrounding_whitespace_on_a_password_is_trimmed_at_sign_up_and_sign_in_alike(db, typed):
    """DRF trims both sides, consistently: a password typed with a stray space
    at registration still signs in without it, and the other way round."""
    assert _register("trim@example.com", password=typed).status_code == 201
    assert _login("trim@example.com", PASSWORD).status_code == 200
    assert _login("trim@example.com", typed).status_code == 200


@pytest.mark.parametrize("identifier", [EMAIL, "01012345678"])
def test_a_disabled_account_cannot_sign_in_by_email_or_phone(account, identifier):
    User.objects.filter(pk=account.pk).update(is_active=False)
    response = _login(identifier, PASSWORD)
    assert response.status_code in (401, 403)
    assert "access" not in (response.json().get("data") or {})


# ---------------------------------------------------------------------------
# Registration: duplicates in any spelling
# ---------------------------------------------------------------------------


def _register(email, phone=None, password="Brand-New-2026!x"):
    body = {"email": email, "password": password, "first_name": "N", "last_name": "U"}
    if phone is not None:
        body["phone"] = phone
    return APIClient().post(REGISTER, body, format="json")


@pytest.mark.parametrize("spelling", [pytest.param(v, id=k) for k, v in EMAIL_SPELLINGS.items()])
def test_register_refuses_the_same_email_in_any_case(account, spelling):
    response = _register(spelling)
    assert response.status_code == 409, (spelling, response.content)
    assert _code(response) == "AUTH_EMAIL_ALREADY_EXISTS"
    assert User.objects.count() == 1


@pytest.mark.parametrize("spelling", [pytest.param(v, id=k) for k, v in PHONE_SPELLINGS.items()])
def test_register_refuses_the_same_phone_in_any_format(account, spelling):
    response = _register("another.person@example.com", spelling)
    assert response.status_code in (400, 409), (spelling, response.content)
    if response.status_code == 409:
        assert _code(response) == "AUTH_PHONE_ALREADY_EXISTS"
    assert User.objects.count() == 1


@pytest.mark.parametrize("spelling", ["01012345678", "+201012345678", "+20 101 234 5678", "201012345678"])
def test_register_stores_every_phone_spelling_as_one_number(db, spelling):
    response = _register("first@example.com", spelling)
    assert response.status_code == 201, response.content
    assert str(User.objects.get(email="first@example.com").phone) == PHONE_E164
    # ... so the number then signs in, whichever way it was written at sign-up.
    assert _login("01012345678", "Brand-New-2026!x").status_code == 200


@pytest.mark.parametrize("spelling", [pytest.param(v, id=k) for k, v in EMAIL_SPELLINGS.items()])
def test_register_stores_the_email_lower_cased_and_trimmed(db, spelling):
    response = _register(spelling)
    assert response.status_code == 201, (spelling, response.content)
    assert User.objects.get().email == EMAIL
    assert _login(EMAIL.upper(), "Brand-New-2026!x").status_code == 200


# ---------------------------------------------------------------------------
# Password reset request
# ---------------------------------------------------------------------------

SEND = "apps.users.views.send_email_task.delay"


@pytest.mark.parametrize("spelling", [pytest.param(v, id=k) for k, v in EMAIL_SPELLINGS.items()])
def test_reset_for_a_registered_email_in_any_case_sends_one_link(account, spelling):
    with mock.patch(SEND) as send:
        response = APIClient().post(RESET, {"email": spelling}, format="json")
    assert response.status_code == 200, (spelling, response.content)
    calls = [c for c in send.call_args_list if c.kwargs.get("event") == "password_reset"]
    assert len(calls) == 1
    assert calls[0].kwargs["to"] == EMAIL
    assert PasswordResetToken.objects.filter(user=account, used_at__isnull=True).count() == 1


@pytest.mark.parametrize("email", ["nobody@example.com", "jane.doe@example.org", "JANE.DOE@EXAMPLE.CO"])
def test_reset_for_an_unknown_email_says_so_and_sends_nothing(account, email):
    with mock.patch(SEND) as send:
        response = APIClient().post(RESET, {"email": email}, format="json")
    assert response.status_code == 404, response.content
    assert _code(response) == "AUTH_EMAIL_NOT_FOUND"
    assert not send.called
    assert not PasswordResetToken.objects.exists()


@pytest.mark.parametrize("email", ["", "not-an-email", "jane.doe@", "@example.com", "01012345678", None])
def test_reset_for_a_malformed_email_is_a_validation_error(account, email):
    with mock.patch(SEND) as send:
        response = APIClient().post(RESET, {"email": email} if email is not None else {}, format="json")
    assert response.status_code == 400, response.content
    assert _code(response) == "VALIDATION_ERROR"
    assert not send.called


# ---------------------------------------------------------------------------
# Password reset confirm: tokens
# ---------------------------------------------------------------------------


def _confirm(token, password="Fresh-Pass-2026!x"):
    return APIClient().post(RESET_CONFIRM, {"token": token, "new_password": password}, format="json")


def test_a_reset_token_works_once_and_then_the_new_password_signs_in(account):
    from apps.users.services import issue_password_reset_token

    token = issue_password_reset_token(account)
    assert _confirm(token).status_code == 200
    again = _confirm(token, "Second-Pass-2026!x")
    assert again.status_code == 400 and _code(again) == "AUTH_OTP_EXPIRED"

    assert _login(EMAIL, PASSWORD).status_code == 401
    assert _login(EMAIL.upper(), "Fresh-Pass-2026!x").status_code == 200
    assert _login("01012345678", "Fresh-Pass-2026!x").status_code == 200


@pytest.mark.parametrize("minutes_left", [-60, -1])
def test_an_expired_reset_token_is_refused(account, minutes_left):
    from apps.users.services import issue_password_reset_token

    token = issue_password_reset_token(account)
    PasswordResetToken.objects.filter(token=token).update(expires_at=timezone.now() + timedelta(minutes=minutes_left))
    response = _confirm(token)
    assert response.status_code == 400 and _code(response) == "AUTH_OTP_EXPIRED"
    account.refresh_from_db()
    assert account.check_password(PASSWORD)


def test_a_reset_token_still_valid_one_minute_before_expiry(account):
    from apps.users.services import issue_password_reset_token

    token = issue_password_reset_token(account)
    PasswordResetToken.objects.filter(token=token).update(expires_at=timezone.now() + timedelta(minutes=1))
    assert _confirm(token).status_code == 200


@pytest.mark.parametrize("token", ["", "garbage", "x" * 43])
def test_a_made_up_reset_token_is_refused(account, token):
    response = _confirm(token)
    assert response.status_code == 400
    account.refresh_from_db()
    assert account.check_password(PASSWORD)


def test_a_reset_ends_every_session_the_old_password_opened(account):
    from apps.users.services import issue_password_reset_token

    refresh = _login(EMAIL, PASSWORD).json()["data"]["refresh"]
    assert _confirm(issue_password_reset_token(account)).status_code == 200
    response = APIClient().post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json")
    assert response.status_code == 401


def test_a_weak_new_password_is_refused_and_the_token_survives(account):
    from apps.users.services import issue_password_reset_token

    token = issue_password_reset_token(account)
    assert _confirm(token, "12345678").status_code == 400
    assert _confirm(token).status_code == 200


def test_requesting_two_resets_leaves_both_links_usable_until_one_is_spent(account):
    with mock.patch(SEND):
        APIClient().post(RESET, {"email": EMAIL}, format="json")
        APIClient().post(RESET, {"email": EMAIL.upper()}, format="json")
    tokens = list(PasswordResetToken.objects.filter(user=account).values_list("token", flat=True))
    assert len(tokens) == 2
    assert _confirm(tokens[0]).status_code == 200


# ---------------------------------------------------------------------------
# Social sign-up with a mixed-case email
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("social_email", ["Ahmed.Ali@Gmail.com", "AHMED.ALI@GMAIL.COM", " ahmed.ali@gmail.com "])
def test_a_social_account_with_a_mixed_case_email_is_one_account_everywhere(db, social_email):
    from apps.social_auth.services import resolve_user

    user, created = resolve_user("google", "g-42", social_email.strip(), True, "Ahmed", "Ali")
    assert created and user.email == "ahmed.ali@gmail.com"

    # The same person, a second provider, another casing: the same account.
    again, created = resolve_user("facebook", "fb-42", "ahmed.ali@GMAIL.com", True, "Ahmed", "Ali")
    assert not created and again.pk == user.pk
    assert User.objects.count() == 1

    # Registering by hand with the address is refused in any case.
    assert _register("Ahmed.Ali@gmail.COM").status_code == 409

    # A password reset finds them in any case, and the new password signs in.
    with mock.patch(SEND) as send:
        response = APIClient().post(RESET, {"email": social_email.upper()}, format="json")
    assert response.status_code == 200
    assert send.call_args.kwargs["to"] == "ahmed.ali@gmail.com"
    token = PasswordResetToken.objects.get(user=user).token
    assert _confirm(token).status_code == 200
    assert _login(social_email, "Fresh-Pass-2026!x").status_code == 200


# ---------------------------------------------------------------------------
# Lockout
# ---------------------------------------------------------------------------

THRESHOLD = 10


@pytest.mark.parametrize(
    "fail_with,then_with",
    [
        (EMAIL, EMAIL),
        (EMAIL.upper(), EMAIL),
        ("01012345678", EMAIL),
        (EMAIL, "+20 101 234 5678"),
        ("+201012345678", "٠١٠١٢٣٤٥٦٧٨"),
    ],
)
def test_the_account_locks_after_n_failures_whatever_spelling_was_used(account, no_throttle, fail_with, then_with):
    for attempt in range(THRESHOLD):
        response = _login(fail_with, WRONG)
        assert response.status_code == 401, (attempt, response.content)
    locked = _login(then_with, PASSWORD)
    assert locked.status_code == 429, locked.content
    assert _code(locked) == "AUTH_ACCOUNT_LOCKED"


def test_one_failure_short_of_the_threshold_does_not_lock(account, no_throttle):
    for _ in range(THRESHOLD - 1):
        assert _login(EMAIL, WRONG).status_code == 401
    assert _login(EMAIL, PASSWORD).status_code == 200


def test_a_successful_login_resets_the_failure_count(account, no_throttle):
    for _ in range(THRESHOLD - 1):
        _login(EMAIL, WRONG)
    assert _login(EMAIL, PASSWORD).status_code == 200
    for _ in range(THRESHOLD - 1):
        assert _login(EMAIL, WRONG).status_code == 401
    assert _login(EMAIL, PASSWORD).status_code == 200


def test_failures_on_someone_else_never_lock_this_account(account, no_throttle):
    for _ in range(THRESHOLD + 2):
        _login("someone.else@example.com", WRONG)
        _login("01099999999", WRONG)
    assert _login(EMAIL, PASSWORD).status_code == 200


def test_the_lock_lifts_once_its_time_is_up(account, no_throttle):
    from django.core.cache import cache

    from apps.users.services import _login_lock_cache_key

    for _ in range(THRESHOLD):
        _login(EMAIL, WRONG)
    assert _login(EMAIL, PASSWORD).status_code == 429
    cache.delete(_login_lock_cache_key(EMAIL))  # the lock's timeout elapsing
    assert _login(EMAIL, PASSWORD).status_code == 200
