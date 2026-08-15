from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel


class GiftCardStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REDEEMED = "redeemed", "Fully redeemed"
    EXPIRED = "expired", "Expired"
    DISABLED = "disabled", "Disabled"


class GiftCard(BaseModel):
    """
    The raw `code` is required to redeem, so it must be stored — but it is
    never included in logs (see apps.core.logging_filters) and API
    responses mask all but the last 4 characters except at issuance time.
    """

    code = models.CharField(max_length=32, unique=True, db_index=True)
    initial_balance = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    balance = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    status = models.CharField(max_length=16, choices=GiftCardStatus.choices, default=GiftCardStatus.ACTIVE)

    purchaser = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="purchased_gift_cards")
    issued_to_email = models.EmailField(blank=True)
    redeemed_by = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="redeemed_gift_cards")

    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["code"]), models.Index(fields=["status"])]

    def __str__(self):
        return f"GiftCard ***{self.code[-4:]}"

    @property
    def masked_code(self) -> str:
        return f"****-****-{self.code[-4:]}"


class GiftCardTransaction(BaseModel):
    gift_card = models.ForeignKey(GiftCard, on_delete=models.CASCADE, related_name="transactions")
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="gift_card_transactions")
    amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", help_text="Negative for redemption, positive for issuance/reload")
    balance_after = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")

    class Meta:
        ordering = ["-created_at"]


class StoreCreditReason(models.TextChoices):
    REFUND = "refund", "Refund"
    PROMOTIONAL = "promotional", "Promotional credit"
    MANUAL_ADMIN = "manual_admin", "Manual admin credit"
    REDEMPTION = "redemption", "Used at checkout"


class StoreCreditAccount(BaseModel):
    user = models.OneToOneField("users.User", on_delete=models.CASCADE, related_name="store_credit_account")
    balance = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=0)

    def __str__(self):
        return f"StoreCredit<{self.user_id}> {self.balance}"


class StoreCreditTransaction(BaseModel):
    account = models.ForeignKey(StoreCreditAccount, on_delete=models.CASCADE, related_name="transactions")
    amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", help_text="Negative when spent")
    reason = models.CharField(max_length=16, choices=StoreCreditReason.choices)
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="store_credit_transactions")
    created_by = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
