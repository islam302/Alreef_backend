from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel


class PromotionType(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage discount"
    FIXED_AMOUNT = "fixed_amount", "Fixed discount"
    FREE_SHIPPING = "free_shipping", "Free shipping"
    BUY_X_GET_Y = "buy_x_get_y", "Buy X get Y (incl. BOGO)"
    BUNDLE_DISCOUNT = "bundle_discount", "Bundle discount"


class Promotion(BaseModel):
    """
    Automatic promotion — applies at checkout without a customer-entered
    code (spec §32). Flash sales use start_date/end_date; category/brand
    promotions use the `categories`/`brands` scope fields.
    """

    name = models.CharField(max_length=150)
    description = models.CharField(max_length=255, blank=True)
    promotion_type = models.CharField(max_length=20, choices=PromotionType.choices)

    percentage_value = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fixed_amount_value = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)

    buy_quantity = models.PositiveIntegerField(null=True, blank=True, help_text="X in buy X get Y")
    get_quantity = models.PositiveIntegerField(null=True, blank=True, help_text="Y in buy X get Y")
    get_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=100, help_text="Discount applied to the 'get' items — 100 = free"
    )

    products = models.ManyToManyField("catalog.Product", blank=True, related_name="promotions")
    categories = models.ManyToManyField("categories.Category", blank=True, related_name="promotions")
    brands = models.ManyToManyField("brands.Brand", blank=True, related_name="promotions")

    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=0, help_text="Higher priority wins when promotions are not stackable")
    stackable = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-priority"]
        indexes = [models.Index(fields=["is_active", "start_date", "end_date"])]

    def __str__(self):
        return self.name

    @property
    def is_scoped(self) -> bool:
        return self.products.exists() or self.categories.exists() or self.brands.exists()
