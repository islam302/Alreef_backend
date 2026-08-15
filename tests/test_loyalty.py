from decimal import Decimal

import pytest
from djmoney.money import Money

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.loyalty.models import (
    LoyaltyAccount,
    LoyaltyProgramSettings,
    LoyaltyTransaction,
    LoyaltyTransactionType,
)
from apps.loyalty.services import award_points_for_order, redeem_points
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

LOYALTY_URL = "/api/v1/loyalty/"
LOYALTY_REDEEM_URL = "/api/v1/loyalty/redeem/"


def _configure_settings(*, points_per_amount=10, amount_unit="50.00", minimum_redeemable_points=100, redemption_value_per_point="0.10"):
    settings_row = LoyaltyProgramSettings.get_solo()
    settings_row.points_per_amount = points_per_amount
    settings_row.amount_unit = Money(amount_unit, "EGP")
    settings_row.minimum_redeemable_points = minimum_redeemable_points
    settings_row.redemption_value_per_point = Money(redemption_value_per_point, "EGP")
    settings_row.save()
    return settings_row


def _make_order(user, grand_total):
    return Order.objects.create(
        order_number=f"ORD-LOY-{user.id}",
        user=user,
        customer_name="Jane Doe",
        customer_email=user.email,
        customer_phone="+201001234567",
        payment_method="cod",
        subtotal=Decimal(str(grand_total)),
        grand_total=Decimal(str(grand_total)),
    )


def test_award_points_for_order_uses_configured_rate(user):
    _configure_settings(points_per_amount=10, amount_unit="50.00")
    order = _make_order(user, "250.00")  # 250 // 50 = 5 units -> 5 * 10 = 50 points

    points = award_points_for_order(order)

    assert points == 50
    account = LoyaltyAccount.objects.get(user=user)
    assert account.points_balance == 50
    assert account.lifetime_points_earned == 50
    txn = LoyaltyTransaction.objects.get(account=account)
    assert txn.transaction_type == LoyaltyTransactionType.EARN
    assert txn.points == 50
    assert txn.order == order


def test_award_points_for_order_below_one_unit_awards_nothing(user):
    _configure_settings(points_per_amount=10, amount_unit="500.00")
    order = _make_order(user, "100.00")  # 100 // 500 = 0 units

    points = award_points_for_order(order)

    assert points is None
    assert not LoyaltyAccount.objects.filter(user=user).exists()


def test_redeem_points_below_minimum_fails(user):
    _configure_settings(minimum_redeemable_points=100)
    LoyaltyAccount.objects.create(user=user, points_balance=500, lifetime_points_earned=500)

    with pytest.raises(ApplicationError) as exc_info:
        redeem_points(user=user, points=50)

    assert exc_info.value.code == ErrorCodes.LOYALTY_INSUFFICIENT_POINTS


def test_redeem_points_insufficient_balance_fails(user):
    _configure_settings(minimum_redeemable_points=100)
    LoyaltyAccount.objects.create(user=user, points_balance=100, lifetime_points_earned=100)

    with pytest.raises(ApplicationError) as exc_info:
        redeem_points(user=user, points=150)

    assert exc_info.value.code == ErrorCodes.LOYALTY_INSUFFICIENT_POINTS


def test_redeem_points_success_decrements_balance_and_logs_transaction(user):
    _configure_settings(minimum_redeemable_points=100, redemption_value_per_point="0.10")
    account = LoyaltyAccount.objects.create(user=user, points_balance=200, lifetime_points_earned=200)

    discount_value = redeem_points(user=user, points=150)

    assert discount_value == Money("15.00", "EGP")
    account.refresh_from_db()
    assert account.points_balance == 50
    txn = LoyaltyTransaction.objects.get(account=account, transaction_type=LoyaltyTransactionType.REDEEM)
    assert txn.points == -150


def test_loyalty_account_endpoint_requires_auth(api_client):
    response = api_client.get(LOYALTY_URL)

    assert response.status_code == 401


def test_loyalty_account_endpoint_creates_account_on_first_access(authenticated_client, user):
    response = authenticated_client.get(LOYALTY_URL)

    assert response.status_code == 200
    assert response.json()["data"]["points_balance"] == 0
    assert LoyaltyAccount.objects.filter(user=user).exists()


def test_loyalty_redeem_endpoint_success(authenticated_client, user):
    _configure_settings(minimum_redeemable_points=100, redemption_value_per_point="0.10")
    LoyaltyAccount.objects.create(user=user, points_balance=200, lifetime_points_earned=200)

    response = authenticated_client.post(LOYALTY_REDEEM_URL, {"points": 150}, format="json")

    assert response.status_code == 200
    assert response.json()["data"]["discount_value"] == "15.00"
