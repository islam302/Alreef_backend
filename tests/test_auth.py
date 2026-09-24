import pytest
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.error_codes import ErrorCodes
from apps.users.models import User

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/v1/auth/register/"
LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
ME_URL = "/api/v1/auth/me/"
CHANGE_PASSWORD_URL = "/api/v1/auth/change-password/"

STRONG_PASSWORD = "Str0ng!Passw0rd"


def test_register_success(api_client):
    payload = {
        "email": "newuser@example.com",
        "password": STRONG_PASSWORD,
        "first_name": "New",
        "last_name": "User",
    }
    response = api_client.post(REGISTER_URL, payload, format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "newuser@example.com"
    assert User.objects.filter(email="newuser@example.com").exists()


def test_register_duplicate_email_returns_409(api_client, user):
    """
    RegisterSerializer declares `email` explicitly (bypassing ModelSerializer's
    automatic UniqueValidator) so validate_email()'s ApplicationError is what
    actually fires, producing the documented 409 AUTH_EMAIL_ALREADY_EXISTS.
    """
    payload = {
        "email": user.email,
        "password": STRONG_PASSWORD,
        "first_name": "Dup",
        "last_name": "User",
    }
    response = api_client.post(REGISTER_URL, payload, format="json")

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["errors"]["code"] == ErrorCodes.AUTH_EMAIL_ALREADY_EXISTS


def test_login_success_returns_access_and_refresh(api_client, user):
    response = api_client.post(LOGIN_URL, {"email": user.email, "password": "SuperSecret123"}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "access" in body["data"]
    assert "refresh" in body["data"]
    assert body["data"]["user"]["email"] == user.email


def test_login_wrong_password_returns_401(api_client, user):
    response = api_client.post(LOGIN_URL, {"email": user.email, "password": "WrongPassword1"}, format="json")

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["errors"]["code"] == ErrorCodes.AUTH_INVALID_CREDENTIALS


def test_login_locks_account_after_repeated_failures(api_client, user):
    """Per-account brute-force lockout (apps.users.services) is a second,
    account-keyed layer on top of the IP/token-bucket 'auth' throttle scope."""
    from apps.users.services import LOGIN_LOCKOUT_THRESHOLD

    for _ in range(LOGIN_LOCKOUT_THRESHOLD):
        response = api_client.post(LOGIN_URL, {"email": user.email, "password": "WrongPassword1"}, format="json")
        assert response.status_code == 401

    # Even the *correct* password is now rejected until the lockout expires.
    response = api_client.post(LOGIN_URL, {"email": user.email, "password": "SuperSecret123"}, format="json")
    assert response.status_code == 429
    assert response.json()["errors"]["code"] == ErrorCodes.AUTH_ACCOUNT_LOCKED


def test_login_success_resets_failed_attempt_counter(api_client, user):
    from apps.users.services import is_login_locked, record_failed_login

    for _ in range(3):
        record_failed_login(user.email)

    response = api_client.post(LOGIN_URL, {"email": user.email, "password": "SuperSecret123"}, format="json")
    assert response.status_code == 200
    assert is_login_locked(user.email) is False


def test_token_refresh_returns_new_access_token(api_client, user):
    refresh = RefreshToken.for_user(user)
    response = api_client.post(REFRESH_URL, {"refresh": str(refresh)}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "access" in body["data"]


def test_logout_blacklists_refresh_token_second_use_fails(user):
    from rest_framework.test import APIClient

    client = APIClient()
    refresh = RefreshToken.for_user(user)
    access = refresh.access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(LOGOUT_URL, {"refresh": str(refresh)}, format="json")
    assert response.status_code == 200
    assert response.json()["success"] is True

    # Second use of the same (now blacklisted) refresh token must fail.
    response_again = client.post(LOGOUT_URL, {"refresh": str(refresh)}, format="json")
    assert response_again.status_code == 401
    assert response_again.json()["errors"]["code"] == ErrorCodes.AUTH_TOKEN_INVALID


def test_get_me_returns_profile(authenticated_client, user):
    response = authenticated_client.get(ME_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["email"] == user.email
    assert body["data"]["full_name"] == user.full_name


def test_patch_me_updates_profile(authenticated_client, user):
    response = authenticated_client.patch(ME_URL, {"first_name": "Updated"}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["first_name"] == "Updated"
    user.refresh_from_db()
    assert user.first_name == "Updated"


def test_me_requires_authentication(api_client):
    response = api_client.get(ME_URL)
    assert response.status_code == 401


def test_change_password_success(authenticated_client, user):
    response = authenticated_client.post(
        CHANGE_PASSWORD_URL,
        {"old_password": "SuperSecret123", "new_password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password(STRONG_PASSWORD)


def test_change_password_wrong_old_password_fails(authenticated_client):
    response = authenticated_client.post(
        CHANGE_PASSWORD_URL,
        {"old_password": "NotTheRightPassword1", "new_password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 401
    assert response.json()["errors"]["code"] == ErrorCodes.AUTH_INVALID_CREDENTIALS


def test_an_egyptian_local_mobile_number_is_accepted(authenticated_client, governorate):
    """Both frontends prompt for 01xxxxxxxxx — the local format every customer
    in Egypt will type. Without PHONENUMBER_DEFAULT_REGION the field only
    parsed E.164, so the address form was rejecting the exact shape its own
    placeholder asked for and no customer could check out. The test fixtures
    all used +20..., which is why nothing here noticed."""
    response = authenticated_client.post(
        "/api/v1/addresses/",
        {
            "label": "home",
            "full_name": "Test Customer",
            "phone": "01060604405",
            "governorate": governorate.id,
            "street": "Station St",
            "building": "12",
            "is_default": True,
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    # Normalised on the way in, so everything downstream sees one format.
    assert response.json()["data"]["phone"] == "+201060604405"
