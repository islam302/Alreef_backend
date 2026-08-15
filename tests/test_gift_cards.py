import datetime

import pytest
from django.utils import timezone
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.gift_cards.models import GiftCardTransaction, StoreCreditTransaction
from apps.gift_cards.services import (
    add_store_credit,
    get_or_create_store_credit_account,
    issue_gift_card,
    redeem_gift_card,
    use_store_credit,
)

pytestmark = pytest.mark.django_db

GIFT_CARD_CHECK_URL = "/api/v1/gift-cards/check/"
STORE_CREDIT_URL = "/api/v1/gift-cards/store-credit/"


def test_issue_gift_card_creates_code_and_balance():
    card = issue_gift_card(amount=Money("100.00", "EGP"))

    assert card.code
    assert card.balance == Money("100.00", "EGP")
    assert card.initial_balance == Money("100.00", "EGP")
    txn = GiftCardTransaction.objects.get(gift_card=card)
    assert txn.amount == Money("100.00", "EGP")
    assert txn.balance_after == Money("100.00", "EGP")


def test_redeem_gift_card_decrements_balance_and_creates_transaction():
    card = issue_gift_card(amount=Money("100.00", "EGP"))

    updated = redeem_gift_card(code=card.code, amount=Money("40.00", "EGP"))

    assert updated.balance == Money("60.00", "EGP")
    txn = GiftCardTransaction.objects.filter(gift_card=card, amount=Money("-40.00", "EGP")).first()
    assert txn is not None
    assert txn.balance_after == Money("60.00", "EGP")


def test_redeem_gift_card_more_than_balance_fails():
    card = issue_gift_card(amount=Money("50.00", "EGP"))

    with pytest.raises(ApplicationError) as exc_info:
        redeem_gift_card(code=card.code, amount=Money("100.00", "EGP"))

    assert exc_info.value.code == ErrorCodes.GIFT_CARD_INSUFFICIENT_BALANCE


def test_redeem_expired_gift_card_fails():
    card = issue_gift_card(amount=Money("50.00", "EGP"), expires_at=timezone.now() - datetime.timedelta(days=1))

    with pytest.raises(ApplicationError) as exc_info:
        redeem_gift_card(code=card.code, amount=Money("10.00", "EGP"))

    assert exc_info.value.code == ErrorCodes.GIFT_CARD_EXPIRED


def test_store_credit_add_and_use_round_trip(user):
    add_store_credit(user=user, amount=Money("50.00", "EGP"), reason="promotional")
    account = get_or_create_store_credit_account(user)
    assert account.balance == Money("50.00", "EGP")

    use_store_credit(user=user, amount=Money("20.00", "EGP"))
    account.refresh_from_db()
    assert account.balance == Money("30.00", "EGP")

    spend_txn = StoreCreditTransaction.objects.get(account=account, reason="redemption")
    assert spend_txn.amount == Money("-20.00", "EGP")


def test_use_store_credit_insufficient_balance_fails(user):
    add_store_credit(user=user, amount=Money("10.00", "EGP"), reason="promotional")

    with pytest.raises(ApplicationError) as exc_info:
        use_store_credit(user=user, amount=Money("50.00", "EGP"))

    assert exc_info.value.code == ErrorCodes.VALIDATION_ERROR


def test_gift_card_check_endpoint_valid_code(authenticated_client):
    card = issue_gift_card(amount=Money("75.00", "EGP"))

    response = authenticated_client.post(GIFT_CARD_CHECK_URL, {"code": card.code}, format="json")

    assert response.status_code == 200
    assert response.json()["data"]["masked_code"] == card.masked_code


def test_gift_card_check_endpoint_invalid_code(authenticated_client):
    response = authenticated_client.post(GIFT_CARD_CHECK_URL, {"code": "DOES-NOT-EXIST"}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == ErrorCodes.GIFT_CARD_INVALID


def test_gift_card_check_endpoint_requires_auth(api_client):
    response = api_client.post(GIFT_CARD_CHECK_URL, {"code": "ANY"}, format="json")

    assert response.status_code == 401


def test_store_credit_account_endpoint(authenticated_client, user):
    add_store_credit(user=user, amount=Money("15.00", "EGP"), reason="promotional")

    response = authenticated_client.get(STORE_CREDIT_URL)

    assert response.status_code == 200
    assert response.json()["data"]["balance"] == "15.00"
