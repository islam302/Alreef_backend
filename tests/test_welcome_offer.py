"""
The welcome offer: a percentage the server chose, signed into a token the
browser holds, claimed into a personal coupon after sign-in.

Most of these are the ways a customer might try to get more than they were
offered: edit the token, keep it past its 24 hours, claim it twice, or spend
someone else's code.
"""
import base64
import datetime
import json
import uuid
import zlib

import pytest
from django.core import signing
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.error_codes import ErrorCodes
from apps.coupons import welcome
from apps.coupons.models import Coupon

pytestmark = pytest.mark.django_db

OFFER_URL = "/api/v1/coupons/welcome-offer/"
CLAIM_URL = "/api/v1/coupons/welcome-offer/claim/"
CART_ITEMS_URL = "/api/v1/cart/items/"
CART_COUPON_URL = "/api/v1/cart/coupon/"
CHECKOUT_URL = "/api/v1/checkout/"


def _client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _issue(client=None):
    response = (client or APIClient()).post(OFFER_URL, {}, format="json")
    assert response.status_code == 200, response.content
    return response.json()["data"]


def _token_issued(hours_ago, percentage=10):
    issued = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)).timestamp())
    return signing.dumps({"p": percentage, "t": issued, "n": "x"}, salt=welcome.SALT, compress=True)


# ---------- Issue ------------------------------------------------------------


def test_issue_gives_a_server_chosen_percentage_and_a_day():
    data = _issue()
    assert data["percentage"] in (5, 10)
    assert data["token"]
    expires = datetime.datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    remaining = expires - datetime.datetime.now(datetime.timezone.utc)
    assert datetime.timedelta(hours=23, minutes=59) < remaining <= datetime.timedelta(hours=24)


def test_reissuing_with_a_token_returns_its_own_terms_not_new_ones():
    first = _issue()
    response = APIClient().post(OFFER_URL, {"token": first["token"]}, format="json")
    assert response.status_code == 200
    assert response.json()["data"]["percentage"] == first["percentage"]
    assert response.json()["data"]["expires_at"] == first["expires_at"]


def test_issue_ignores_a_percentage_sent_by_the_client():
    for _ in range(10):
        data = APIClient().post(OFFER_URL, {"percentage": 90}, format="json").json()["data"]
        assert data["percentage"] in (5, 10)


# ---------- Tampering --------------------------------------------------------


def test_a_token_edited_to_90_percent_is_refused(user):
    token = _issue()["token"]
    # Rebuild the payload with p=90 and keep the original signature: exactly
    # what someone editing localStorage by hand would produce.
    payload_b64, rest = token[1:].split(":", 1) if token.startswith(".") else token.split(":", 1)
    forged_payload = base64.urlsafe_b64encode(
        zlib.compress(json.dumps({"p": 90, "t": 0, "n": "x"}, separators=(",", ":")).encode())
    ).decode().rstrip("=")
    forged = f".{forged_payload}:{rest}"

    response = _client_for(user).post(CLAIM_URL, {"token": forged}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.WELCOME_OFFER_INVALID
    assert not Coupon.objects.filter(owner=user).exists()


def test_a_token_signed_with_another_key_is_refused(user):
    forged = signing.dumps({"p": 90, "t": 9999999999, "n": "x"}, key="not-our-key", salt=welcome.SALT)
    response = _client_for(user).post(CLAIM_URL, {"token": forged}, format="json")
    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.WELCOME_OFFER_INVALID


def test_a_genuine_token_for_a_percentage_no_longer_offered_is_refused(user):
    token = _token_issued(hours_ago=1, percentage=40)
    response = _client_for(user).post(CLAIM_URL, {"token": token}, format="json")
    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.WELCOME_OFFER_INVALID


def test_garbage_is_refused(user):
    response = _client_for(user).post(CLAIM_URL, {"token": "90"}, format="json")
    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.WELCOME_OFFER_INVALID


# ---------- Expiry -----------------------------------------------------------


def test_an_offer_older_than_a_day_cannot_be_claimed(user):
    response = _client_for(user).post(CLAIM_URL, {"token": _token_issued(hours_ago=25)}, format="json")
    assert response.status_code == 410
    assert response.json()["errors"]["code"] == ErrorCodes.WELCOME_OFFER_EXPIRED


def test_an_expired_token_is_not_swapped_for_a_fresh_offer():
    response = APIClient().post(OFFER_URL, {"token": _token_issued(hours_ago=25)}, format="json")
    assert response.status_code == 410


def test_the_coupon_expires_when_the_offer_does_not_a_day_after_claiming(user):
    data = _client_for(user).post(CLAIM_URL, {"token": _token_issued(hours_ago=20)}, format="json").json()["data"]
    expires = datetime.datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    remaining = expires - datetime.datetime.now(datetime.timezone.utc)
    assert remaining <= datetime.timedelta(hours=4)


@override_settings(WELCOME_OFFER_HOURS=2)
def test_lifetime_follows_the_setting(user):
    response = _client_for(user).post(CLAIM_URL, {"token": _token_issued(hours_ago=3)}, format="json")
    assert response.status_code == 410


# ---------- Claim ------------------------------------------------------------


def test_claim_requires_sign_in():
    response = APIClient().post(CLAIM_URL, {"token": _issue()["token"]}, format="json")
    assert response.status_code == 401


def test_claim_creates_a_personal_single_use_coupon(user):
    offer = _issue()
    response = _client_for(user).post(CLAIM_URL, {"token": offer["token"]}, format="json")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["code"].startswith("WELCOME-")
    assert data["percentage"] == offer["percentage"]
    coupon = Coupon.objects.get(code=data["code"])
    assert coupon.owner == user
    assert coupon.usage_limit == 1 and coupon.per_user_limit == 1
    assert int(coupon.percentage_value) == offer["percentage"]


def test_claiming_twice_returns_the_same_coupon(user):
    client = _client_for(user)
    first = client.post(CLAIM_URL, {"token": _issue()["token"]}, format="json").json()["data"]
    second = client.post(CLAIM_URL, {"token": _issue()["token"]}, format="json").json()["data"]
    assert first["code"] == second["code"]
    assert Coupon.objects.filter(owner=user).count() == 1


def test_get_returns_the_live_coupon_or_null(user):
    client = _client_for(user)
    assert client.get(CLAIM_URL).json()["data"] is None
    code = client.post(CLAIM_URL, {"token": _issue()["token"]}, format="json").json()["data"]["code"]
    assert client.get(CLAIM_URL).json()["data"]["code"] == code


def test_an_account_that_has_ordered_is_not_eligible(delivered_order, user):
    response = _client_for(user).post(CLAIM_URL, {"token": _issue()["token"]}, format="json")
    assert response.status_code == 409
    assert response.json()["errors"]["code"] == ErrorCodes.WELCOME_OFFER_NOT_ELIGIBLE


# ---------- Spending it ------------------------------------------------------


def test_the_owner_can_apply_it(user, product_with_variant_and_stock):
    client = _client_for(user)
    code = client.post(CLAIM_URL, {"token": _issue()["token"]}, format="json").json()["data"]["code"]
    client.post(CART_ITEMS_URL, {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1}, format="json")

    response = client.post(CART_COUPON_URL, {"code": code}, format="json")

    assert response.status_code == 200
    assert response.json()["data"]["coupon_code"] == code


def test_another_account_cannot_apply_it(user, other_user, product_with_variant_and_stock):
    code = _client_for(user).post(CLAIM_URL, {"token": _issue()["token"]}, format="json").json()["data"]["code"]
    mallory = _client_for(other_user)
    mallory.post(CART_ITEMS_URL, {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1}, format="json")

    response = mallory.post(CART_COUPON_URL, {"code": code}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.INVALID_COUPON
    # And the preview endpoint does not confirm the code exists either.
    assert mallory.get(f"/api/v1/coupons/{code}/").status_code == 400


def test_a_guest_cannot_apply_it(user, product_with_variant_and_stock):
    code = _client_for(user).post(CLAIM_URL, {"token": _issue()["token"]}, format="json").json()["data"]["code"]
    guest = APIClient()
    guest.post(CART_ITEMS_URL, {"variant_id": product_with_variant_and_stock["variant"].id, "quantity": 1}, format="json")

    response = guest.post(CART_COUPON_URL, {"code": code}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.INVALID_COUPON


def test_checkout_applies_it_once(user, product_with_variant_and_stock, address, shipping_setup):
    client = _client_for(user)
    offer = _issue()
    code = client.post(CLAIM_URL, {"token": offer["token"]}, format="json").json()["data"]["code"]
    variant = product_with_variant_and_stock["variant"]
    payload = {
        "address_id": address.id, "shipping_method_id": shipping_setup["method"].id,
        "payment_method": "cod", "coupon_code": code,
    }

    client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    first = client.post(CHECKOUT_URL, payload, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert first.status_code == 201, first.content
    order = first.json()["data"]["order"]
    assert float(order["coupon_discount"]) == pytest.approx(float(order["subtotal"]) * offer["percentage"] / 100)

    # Spent: gone from the account, and a second order cannot reuse it.
    assert client.get(CLAIM_URL).json()["data"] is None
    client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    second = client.post(CHECKOUT_URL, payload, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert second.status_code == 409
    assert second.json()["errors"]["code"] == ErrorCodes.COUPON_USAGE_LIMIT_REACHED


# ---------- Dashboard --------------------------------------------------------


def test_personal_coupons_stay_out_of_the_dashboard_list(user, staff_client):
    _client_for(user).post(CLAIM_URL, {"token": _issue()["token"]}, format="json")
    Coupon.objects.create(code="SHOPWIDE", discount_type="percentage", percentage_value=5)
    admin = staff_client

    listed = admin.get("/api/v1/admin-api/coupons/").json()["data"]
    rows = listed["results"] if isinstance(listed, dict) else listed
    assert [r["code"] for r in rows] == ["SHOPWIDE"]

    personal = admin.get("/api/v1/admin-api/coupons/?personal=true").json()["data"]
    rows = personal["results"] if isinstance(personal, dict) else personal
    assert len(rows) == 1 and rows[0]["owner_email"] == user.email
