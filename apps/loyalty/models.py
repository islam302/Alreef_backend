from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel


class LoyaltyProgramSettings(BaseModel):
    """
    Singleton-style config so earn/redeem rules are admin-configurable
    without a deploy (spec §33): 'keep rules configurable'.
    """

    points_per_amount = models.PositiveIntegerField(default=1, help_text="Points earned per `amount_unit` spent")
    amount_unit = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default=100, help_text="e.g. 100 EGP -> points_per_amount points")
    redemption_value_per_point = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", default="0.10", help_text="Money value of 1 point when redeemed")
    points_expiry_days = models.PositiveIntegerField(null=True, blank=True, help_text="Leave blank for points that never expire")
    minimum_redeemable_points = models.PositiveIntegerField(default=100)

    class Meta:
        verbose_name = "Loyalty program settings"
        verbose_name_plural = "Loyalty program settings"

    def __str__(self):
        return "Loyalty program settings"

    @classmethod
    def get_solo(cls) -> "LoyaltyProgramSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class LoyaltyTier(BaseModel):
    name = models.CharField(max_length=50)
    minimum_points = models.PositiveIntegerField()
    benefits = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["minimum_points"]

    def __str__(self):
        return self.name


class LoyaltyAccount(BaseModel):
    user = models.OneToOneField("users.User", on_delete=models.CASCADE, related_name="loyalty_account")
    points_balance = models.IntegerField(default=0)
    lifetime_points_earned = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"LoyaltyAccount<{self.user_id}> {self.points_balance}pts"

    @property
    def current_tier(self):
        return LoyaltyTier.objects.filter(minimum_points__lte=self.lifetime_points_earned).order_by("-minimum_points").first()


class LoyaltyTransactionType(models.TextChoices):
    EARN = "earn", "Earn"
    REDEEM = "redeem", "Redeem"
    EXPIRE = "expire", "Expire"
    ADJUST = "adjust", "Manual adjustment"


class LoyaltyTransaction(BaseModel):
    account = models.ForeignKey(LoyaltyAccount, on_delete=models.CASCADE, related_name="transactions")
    transaction_type = models.CharField(max_length=16, choices=LoyaltyTransactionType.choices)
    points = models.IntegerField(help_text="Positive for earn, negative for redeem/expire")
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="loyalty_transactions")
    note = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account", "transaction_type"])]
