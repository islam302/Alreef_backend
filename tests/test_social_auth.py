"""
Sign in with Google / Facebook. Google's and Facebook's answers are faked at
the HTTP boundary (requests.get), so every rule about *trusting* them is tested
without the network: the token must be for our app, the email must be vouched
for before it can open an existing account, and a second sign-in finds the
same account by the provider's id even if the email changed.
"""

from unittest import mock

import pytest
import requests

from apps.social_auth.models import SocialAccount
from apps.users.models import User

pytestmark = pytest.mark.django_db

PROVIDERS = "/api/v1/auth/social/providers/"
GOOGLE = "/api/v1/auth/social/google/"
FACEBOOK = "/api/v1/auth/social/facebook/"
CLIENT_ID = "123-test.apps.googleusercontent.com"


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture
def social_settings(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = CLIENT_ID
    settings.FACEBOOK_APP_ID = "999"
    settings.FACEBOOK_APP_SECRET = "fb-secret"
    settings.FACEBOOK_GRAPH_VERSION = "v21.0"
    return settings


def google_info(**over):
    info = {
        "aud": CLIENT_ID, "iss": "https://accounts.google.com", "sub": "g-1",
        "email": "sara@gmail.com", "email_verified": "true", "given_name": "Sara", "family_name": "Ali",
    }
    info.update(over)
    return info


def fake_google(info, status=200):
    return mock.patch("apps.social_auth.services.requests.get", return_value=Reply(status, info))


def fake_facebook(debug=None, me=None):
    debug = debug or {"data": {"is_valid": True, "app_id": "999", "user_id": "fb-7"}}
    me = me or {"id": "fb-7", "email": "omar@example.com", "first_name": "Omar", "last_name": "Hassan"}

    def get(url, params=None, timeout=None):
        return Reply(200, debug if url.endswith("/debug_token") else me)

    return mock.patch("apps.social_auth.services.requests.get", side_effect=get)


def test_providers_lists_only_configured(api_client, settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = CLIENT_ID
    settings.FACEBOOK_APP_ID = "999"
    settings.FACEBOOK_APP_SECRET = ""
    data = api_client.get(PROVIDERS).json()["data"]
    assert data == {"google": {"client_id": CLIENT_ID}}


def test_disabled_provider_is_404(api_client, settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = ""
    response = api_client.post(GOOGLE, {"credential": "x"}, format="json")
    assert response.status_code == 404
    assert response.json()["errors"]["code"] == "AUTH_SOCIAL_PROVIDER_DISABLED"


def test_google_creates_a_verified_account_and_signs_in(api_client, social_settings):
    with fake_google(google_info()):
        response = api_client.post(GOOGLE, {"credential": "id-token"}, format="json")
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["created"] is True and data["access"] and data["refresh"]
    user = User.objects.get(email="sara@gmail.com")
    assert user.email_verified and not user.has_usable_password()
    assert (user.first_name, user.last_name) == ("Sara", "Ali")
    assert SocialAccount.objects.filter(user=user, provider="google", uid="g-1").exists()

    me = api_client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {data['access']}")
    assert me.status_code == 200


def test_google_token_for_another_app_is_refused(api_client, social_settings):
    with fake_google(google_info(aud="someone-else.apps.googleusercontent.com")):
        response = api_client.post(GOOGLE, {"credential": "id-token"}, format="json")
    assert response.status_code == 401
    assert not User.objects.filter(email="sara@gmail.com").exists()


def test_google_rejected_token_is_refused(api_client, social_settings):
    with fake_google({"error": "invalid_token"}, status=400):
        response = api_client.post(GOOGLE, {"credential": "forged"}, format="json")
    assert response.status_code == 401


def test_unverified_google_email_cannot_open_an_existing_account(api_client, social_settings, user):
    with fake_google(google_info(email=user.email, email_verified="false")):
        response = api_client.post(GOOGLE, {"credential": "id-token"}, format="json")
    assert response.status_code == 401
    assert not SocialAccount.objects.exists()


def test_verified_email_links_the_existing_account(api_client, social_settings, user):
    with fake_google(google_info(email=user.email.upper())):
        response = api_client.post(GOOGLE, {"credential": "id-token"}, format="json")
    assert response.status_code == 200
    assert response.json()["data"]["created"] is False
    assert response.json()["data"]["user"]["id"] == user.id
    assert User.objects.count() == 1
    user.refresh_from_db()
    assert user.email_verified and user.check_password("SuperSecret123")


def test_second_sign_in_finds_the_account_by_provider_id(api_client, social_settings):
    with fake_google(google_info()):
        first = api_client.post(GOOGLE, {"credential": "a"}, format="json").json()["data"]
    with fake_google(google_info(email="sara.new@gmail.com")):
        second = api_client.post(GOOGLE, {"credential": "b"}, format="json").json()["data"]
    assert second["user"]["id"] == first["user"]["id"]
    assert User.objects.count() == 1


def test_disabled_account_cannot_sign_in(api_client, social_settings, user):
    user.is_active = False
    user.save()
    with fake_google(google_info(email=user.email)):
        response = api_client.post(GOOGLE, {"credential": "id-token"}, format="json")
    assert response.status_code == 403


def test_facebook_creates_an_account(api_client, social_settings):
    with fake_facebook() as get:
        response = api_client.post(FACEBOOK, {"access_token": "fb-token"}, format="json")
    assert response.status_code == 201
    assert User.objects.get(email="omar@example.com").email_verified
    me_call = [c for c in get.call_args_list if c.args[0].endswith("/me")][0]
    assert me_call.kwargs["params"]["appsecret_proof"]  # replayed tokens are rejected by Facebook


def test_facebook_token_for_another_app_is_refused(api_client, social_settings):
    with fake_facebook(debug={"data": {"is_valid": True, "app_id": "111", "user_id": "fb-7"}}):
        response = api_client.post(FACEBOOK, {"access_token": "fb-token"}, format="json")
    assert response.status_code == 401
    assert not User.objects.exists()


def test_facebook_without_email_explains(api_client, social_settings):
    with fake_facebook(me={"id": "fb-7", "first_name": "Omar"}):
        response = api_client.post(FACEBOOK, {"access_token": "fb-token"}, format="json")
    assert response.status_code == 400
    assert response.json()["errors"]["code"] == "AUTH_SOCIAL_EMAIL_REQUIRED"


def test_provider_down_is_a_503_not_a_crash(api_client, social_settings):
    with mock.patch("apps.social_auth.services.requests.get", side_effect=requests.ConnectionError):
        response = api_client.post(GOOGLE, {"credential": "id-token"}, format="json")
    assert response.status_code == 503

