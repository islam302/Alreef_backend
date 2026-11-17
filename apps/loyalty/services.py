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

    # Order confirmation can run more than once for the same order — a
    # cash-on-delivery order confirms at checkout and again when the cash is
    # collected, and a gateway may re-deliver its webhook. Awarding again each
    # time would hand out points the customer never earned, so the earning
    # transaction is the idempotency record.
    if LoyaltyTransaction.objects.filter(
        account=account, order=order, transaction_type=LoyaltyTransactionType.EARN
    ).exists():
        return None

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

    # get_or_create first: a customer who never earned a point has no row yet,
    # and a bare .get() turned their redeem attempt into a 500.
    account = get_or_create_account(user)
    account = LoyaltyAccount.objects.select_for_update().get(pk=account.pk)
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
    )

    count = 0
    for txn in expired:
        # Idempotency is per earning, not per account: excluding every account
        # that already had one EXPIRE row meant nothing it earned later could
        # ever expire. The EXPIRE row's note names the earning it closed.
        if LoyaltyTransaction.objects.filter(
            account_id=txn.account_id, transaction_type=LoyaltyTransactionType.EXPIRE,
            note=f"Expired from earn txn {txn.id}",
        ).exists():
            continue
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


@transaction.atomic
def reverse_points_for_order(order, *, note: str, fraction=1):
    """
    Take back points an order earned that it no longer deserves.

    Points are awarded at confirmation — for COD that is at checkout, before a
    piastre is paid — and nothing took them back when the order was cancelled
    or refunded, so a cancelled order still paid out its points. `fraction` is
    the share of the order given back (1 for a cancellation). Idempotent on
    `note`, which names the event; never drives the balance below zero (points
    already spent or expired stay gone).
    """
    from decimal import Decimal

    if order.user_id is None:
        return 0
    earned = LoyaltyTransaction.objects.filter(
        order=order, transaction_type=LoyaltyTransactionType.EARN
    ).first()
    if earned is None:
        return 0
    account = LoyaltyAccount.objects.select_for_update().get(pk=earned.account_id)
    if LoyaltyTransaction.objects.filter(
        account=account, order=order, transaction_type=LoyaltyTransactionType.ADJUST, note=note
    ).exists():
        return 0
    already = -sum(
        LoyaltyTransaction.objects.filter(
            account=account, order=order, transaction_type=LoyaltyTransactionType.ADJUST, points__lt=0
        ).values_list("points", flat=True)
    )
    wanted = int((Decimal(earned.points) * Decimal(str(fraction))).to_integral_value())
    points = max(0, min(wanted, earned.points - already, account.points_balance))
    if points <= 0:
        return 0
    LoyaltyTransaction.objects.create(
        account=account, transaction_type=LoyaltyTransactionType.ADJUST, points=-points, order=order, note=note[:255],
    )
    account.points_balance -= points
    account.save(update_fields=["points_balance"])
    return points
