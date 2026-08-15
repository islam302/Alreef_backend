from django.core.validators import MinValueValidator
from django.db import models
from djmoney.models.fields import MoneyField

from apps.core.models import BaseModel

EGYPT_GOVERNORATES = [
    ("cairo", "Cairo", "القاهرة"),
    ("giza", "Giza", "الجيزة"),
    ("alexandria", "Alexandria", "الإسكندرية"),
    ("qalyubia", "Qalyubia", "القليوبية"),
    ("sharqia", "Sharqia", "الشرقية"),
    ("dakahlia", "Dakahlia", "الدقهلية"),
    ("gharbia", "Gharbia", "الغربية"),
    ("monufia", "Monufia", "المنوفية"),
    ("beheira", "Beheira", "البحيرة"),
    ("kafr_el_sheikh", "Kafr El Sheikh", "كفر الشيخ"),
    ("damietta", "Damietta", "دمياط"),
    ("port_said", "Port Said", "بورسعيد"),
    ("ismailia", "Ismailia", "الإسماعيلية"),
    ("suez", "Suez", "السويس"),
    ("fayoum", "Fayoum", "الفيوم"),
    ("beni_suef", "Beni Suef", "بني سويف"),
    ("minya", "Minya", "المنيا"),
    ("assiut", "Assiut", "أسيوط"),
    ("sohag", "Sohag", "سوهاج"),
    ("qena", "Qena", "قنا"),
    ("luxor", "Luxor", "الأقصر"),
    ("aswan", "Aswan", "أسوان"),
    ("red_sea", "Red Sea", "البحر الأحمر"),
    ("new_valley", "New Valley", "الوادي الجديد"),
    ("matrouh", "Matrouh", "مطروح"),
    ("north_sinai", "North Sinai", "شمال سيناء"),
    ("south_sinai", "South Sinai", "جنوب سيناء"),
]


class Governorate(BaseModel):
    """Database-driven Egyptian governorates (spec §45). Not hard-coded prices here."""

    code = models.SlugField(max_length=32, unique=True)
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name_en"]

    def __str__(self):
        return self.name_en


class City(BaseModel):
    governorate = models.ForeignKey(Governorate, on_delete=models.CASCADE, related_name="cities")
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name_en"]
        unique_together = [("governorate", "name_en")]

    def __str__(self):
        return f"{self.name_en}, {self.governorate.name_en}"


class ShippingZone(BaseModel):
    """A named group of governorates sharing shipping methods/rates."""

    name = models.CharField(max_length=100)
    governorates = models.ManyToManyField(Governorate, related_name="zones")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class ShippingMethodType(models.TextChoices):
    STANDARD = "standard", "Standard"
    EXPRESS = "express", "Express"
    SAME_DAY = "same_day", "Same day"
    PICKUP = "pickup", "Store pickup"


class ShippingMethod(BaseModel):
    """Configurable shipping method (e.g. Standard, Express). Rates live in ShippingRate."""

    name = models.CharField(max_length=100)
    method_type = models.CharField(max_length=16, choices=ShippingMethodType.choices, default=ShippingMethodType.STANDARD)
    description = models.CharField(max_length=255, blank=True)
    carrier_code = models.CharField(max_length=32, blank=True, help_text="e.g. bosta, aramex, self")
    min_delivery_days = models.PositiveSmallIntegerField(default=1)
    max_delivery_days = models.PositiveSmallIntegerField(default=3)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class RateCalculationType(models.TextChoices):
    FLAT = "flat", "Flat rate"
    WEIGHT_BASED = "weight_based", "Weight based"
    PRICE_BASED = "price_based", "Order price based"
    FREE = "free", "Free shipping"


class ShippingRate(BaseModel):
    """
    Zone + method -> price. Weight/price-based rates use min/max bounds;
    free_shipping_threshold overrides the computed rate when the order
    subtotal meets it. Nothing here is hard-coded into Order/Address logic.
    """

    zone = models.ForeignKey(ShippingZone, on_delete=models.CASCADE, related_name="rates")
    method = models.ForeignKey(ShippingMethod, on_delete=models.CASCADE, related_name="rates")
    calculation_type = models.CharField(max_length=16, choices=RateCalculationType.choices, default=RateCalculationType.FLAT)
    base_price = MoneyField(max_digits=10, decimal_places=2, default_currency="EGP", default=0)
    price_per_kg = MoneyField(max_digits=10, decimal_places=2, default_currency="EGP", default=0)
    min_weight_kg = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    max_weight_kg = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    min_order_amount = MoneyField(max_digits=10, decimal_places=2, default_currency="EGP", null=True, blank=True)
    free_shipping_threshold = MoneyField(max_digits=10, decimal_places=2, default_currency="EGP", null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("zone", "method")]

    def __str__(self):
        return f"{self.zone} / {self.method}"


class ShipmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PICKED_UP = "picked_up", "Picked up"
    IN_TRANSIT = "in_transit", "In transit"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    RETURNED = "returned", "Returned to sender"
    CANCELLED = "cancelled", "Cancelled"


class Shipment(BaseModel):
    """Created once an order is packed. `order` FK declared as string to avoid app coupling."""

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="shipments")
    shipping_method = models.ForeignKey(ShippingMethod, on_delete=models.PROTECT, related_name="shipments")
    carrier_code = models.CharField(max_length=32, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True, db_index=True)
    tracking_url = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=ShipmentStatus.choices, default=ShipmentStatus.PENDING)
    estimated_delivery = models.DateField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)])
    provider_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["tracking_number"]), models.Index(fields=["status"])]

    def __str__(self):
        return f"Shipment #{self.pk} for order {self.order_id}"


class ShipmentEvent(BaseModel):
    """Append-only tracking history, populated by carrier webhooks or manual updates."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=20, choices=ShipmentStatus.choices)
    description = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField()
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["occurred_at"]
