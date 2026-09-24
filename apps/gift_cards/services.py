import secrets

from django.db import transaction
from django.utils import timezone

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.gift_cards.models import (
    GiftCard,
    GiftCardStatus,
    GiftCardTransaction,
    StoreCreditAccount,
    StoreCreditTransaction,
)


def _generate_code() -> str:
    return "-".join(secrets.token_hex(2).upper() for _ in range(4))


@transaction.atomic
def issue_gift_card(*, amount, purchaser=None, issued_to_email: str = "", expires_at=None) -> GiftCard:
    code = _generate_code()
    while GiftCard.objects.filter(code=code).exists():
        code = _generate_code()
    gift_card = GiftCard.objects.create(
        code=code, initial_balance=amount, balance=amount, purchaser=purchaser,
        issued_to_email=issued_to_email, expires_at=expires_at,
    )
    GiftCardTransaction.objects.create(gift_card=gift_card, amount=amount, balance_after=gift_card.balance)
    return gift_card


def get_valid_gift_card(code: str) -> GiftCard:
    gift_card = GiftCard.objects.filter(code=code).first()
    if not gift_card or gift_card.status == GiftCardStatus.DISABLED:
        raise ApplicationError(ErrorCodes.GIFT_CARD_INVALID, "Gift card code is invalid.")
    if gift_card.expires_at and gift_card.expires_at < timezone.now():
        raise ApplicationError(ErrorCodes.GIFT_CARD_EXPIRED, "Gift card has expired.")
    if gift_card.balance.amount <= 0:
        raise ApplicationError(ErrorCodes.GIFT_CARD_INSUFFICIENT_BALANCE, "Gift card has no remaining balance.")
    return gift_card


@transaction.atomic
def redeem_gift_card(*, code: str, amount, order=None, user=None) -> GiftCard:
    gift_card = GiftCard.objects.select_for_update().get(code=code)
    gift_card = get_valid_gift_card(gift_card.code)  # re-validate under lock
    if gift_card.balance < amount:
        raise ApplicationError(ErrorCodes.GIFT_CARD_INSUFFICIENT_BALANCE, "Gift card balance is insufficient for this amount.")

    gift_card.balance -= amount
    gift_card.status = GiftCardStatus.REDEEMED if gift_card.balance.amount == 0 else GiftCardStatus.ACTIVE
    if user:
        gift_card.redeemed_by = user
    gift_card.save(update_fields=["balance", "status", "redeemed_by"])
    GiftCardTransaction.objects.create(gift_card=gift_card, order=order, amount=-amount, balance_after=gift_card.balance)
    return gift_card


def get_or_create_store_credit_account(user) -> StoreCreditAccount:
    account, _ = StoreCreditAccount.objects.get_or_create(user=user)
    return account


@transaction.atomic
def add_store_credit(*, user, amount, reason: str, order=None, created_by=None) -> StoreCreditAccount:
    account = StoreCreditAccount.objects.select_for_update().get_or_create(user=user)[0]
    account.balance += amount
    account.save(update_fields=["balance"])
    StoreCreditTransaction.objects.create(account=account, amount=amount, reason=reason, order=order, created_by=created_by)
    return account


@transaction.atomic
def use_store_credit(*, user, amount, order=None) -> StoreCreditAccount:
    account = StoreCreditAccount.objects.select_for_update().get(user=user)
    if account.balance < amount:
        raise ApplicationError(ErrorCodes.VALIDATION_ERROR, "Insufficient store credit balance.")
    account.balance -= amount
    account.save(update_fields=["balance"])
    StoreCreditTransaction.objects.create(account=account, amount=-amount, reason="redemption", order=order)
    return account
