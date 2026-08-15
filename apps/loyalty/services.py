from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.loyalty.models import (
    LoyaltyAccount,
    LoyaltyProgramSettings,
    LoyaltyTransaction,
    LoyaltyTransactionType,
)


def get_or_create_account(user) -> LoyaltyAccount:
    account, _ = LoyaltyAccount.objects.get_or_create(user=user)
    return account


@transaction.atomic
def award_points_for_order(order):
    settings_row = LoyaltyProgramSettings.get_solo()
    if settings_row.amount_unit.amount <= 0:
        return None

    units = int(order.grand_total.amount // settings_row.amount_unit.amount)
    points = units * settings_row.points_per_amount
    if points <= 0:
        return None

    account = get_or_create_account(order.user)
    account = LoyaltyAccount.objects.select_for_update().get(pk=account.pk)
    expires_at = timezone.now() + timedelta(days=settings_row.points_expiry_days) if settings_row.points_expiry_days else None

    LoyaltyTransaction.objects.create(
        account=account, transaction_type=LoyaltyTransactionType.EARN, points=points, order=order,
        note=f"Earned from order {order.order_number}", expires_at=expires_at,
    )
    account.points_balance += points
    account.lifetime_points_earned += points
    account.save(update_fields=["points_balance", "lifetime_points_earned"])
    return points


@transaction.atomic
def redeem_points(*, user, points: int):
    settings_row = LoyaltyProgramSettings.get_solo()
    if points < settings_row.minimum_redeemable_points:
        raise ApplicationError(
            ErrorCodes.LOYALTY_INSUFFICIENT_POINTS,
            f"Minimum redeemable is {settings_row.minimum_redeemable_points} points.",
        )

    account = LoyaltyAccount.objects.select_for_update().get(user=user)
    if account.points_balance < points:
        raise ApplicationError(ErrorCodes.LOYALTY_INSUFFICIENT_POINTS, "Not enough points.")

    LoyaltyTransaction.objects.create(account=account, transaction_type=LoyaltyTransactionType.REDEEM, points=-points)
    account.points_balance -= points
    account.save(update_fields=["points_balance"])

    discount_value = settings_row.redemption_value_per_point * points
    return discount_value


@transaction.atomic
def expire_stale_points():
    """Celery Beat: expires points past their `expires_at`."""
    expired = LoyaltyTransaction.objects.filter(
        transaction_type=LoyaltyTransactionType.EARN, expires_at__isnull=False, expires_at__lt=timezone.now(),
    ).exclude(account__transactions__transaction_type=LoyaltyTransactionType.EXPIRE)

    count = 0
    for txn in expired:
        account = LoyaltyAccount.objects.select_for_update().get(pk=txn.account_id)
        if account.points_balance <= 0:
            continue
        expire_amount = min(txn.points, account.points_balance)
        LoyaltyTransaction.objects.create(
            account=account, transaction_type=LoyaltyTransactionType.EXPIRE, points=-expire_amount,
            note=f"Expired from earn txn {txn.id}",
        )
        account.points_balance -= expire_amount
        account.save(update_fields=["points_balance"])
        count += 1
    return count
