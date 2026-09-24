from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel


class CouponDiscountType(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage"
    FIXED_AMOUNT = "fixed_amount", "Fixed amount"
    FREE_SHIPPING = "free_shipping", "Free shipping"


class Coupon(BaseModel):
    code = models.CharField(max_length=50, unique=True, db_index=True)
    description = models.CharField(max_length=255, blank=True)
    discount_type = models.CharField(max_length=16, choices=CouponDiscountType.choices)
    percentage_value = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="0-100, used when discount_type=percentage")
    fixed_amount_value = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)

    minimum_order_amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)
    maximum_discount_amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)

    start_date = models.DateTimeField(null=True, blank=True)
    expiration_date = models.DateTimeField(null=True, blank=True)

    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Total redemptions allowed across all customers")
    per_user_limit = models.PositiveIntegerField(null=True, blank=True, default=1)

    products = models.ManyToManyField("catalog.Product", blank=True, related_name="coupons")
    categories = models.ManyToManyField("categories.Category", blank=True, related_name="coupons")
    brands = models.ManyToManyField("brands.Brand", blank=True, related_name="coupons")

    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["code"]), models.Index(fields=["is_active"])]

    def __str__(self):
        return self.code

    @property
    def times_used(self) -> int:
        return self.usages.count()

    @property
    def is_scoped(self) -> bool:
        return self.products.exists() or self.categories.exists() or self.brands.exists()


class CouponUsage(BaseModel):
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="usages")
    user = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="coupon_usages")
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="coupon_usages")
    discount_amount = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["coupon", "user"])]
