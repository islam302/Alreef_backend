from django.contrib.postgres.fields import ArrayField
from django.core.validators import MinValueValidator
from django.db import models
from djmoney.models.fields import MoneyField

from apps.catalog import codes
from apps.core.models import BaseModel
from apps.core.validators import validate_image_file


class ProductType(models.TextChoices):
    SIMPLE = "simple", "Simple"
    VARIABLE = "variable", "Variable (has variants)"
    BUNDLE = "bundle", "Bundle"
    MIX_AND_MATCH = "mix_and_match", "Mix & match"
    SUBSCRIPTION = "subscription", "Subscription"


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class PackagingType(models.TextChoices):
    JAR = "jar", "Jar"
    BOTTLE = "bottle", "Bottle"
    BOX = "box", "Box"
    BAG = "bag", "Bag"
    CAN = "can", "Can"
    POUCH = "pouch", "Pouch"
    GIFT_BOX = "gift_box", "Gift box"


class ProductBadgeCode(models.TextChoices):
    NEW = "NEW", "New"
    BEST_SELLER = "BEST_SELLER", "Best seller"
    SALE = "SALE", "Sale"
    LIMITED = "LIMITED", "Limited"
    ORGANIC = "ORGANIC", "Organic"
    FEATURED = "FEATURED", "Featured"
    # OUT_OF_STOCK / LOW_STOCK are computed dynamically from inventory, never stored.


class TaxCategory(models.TextChoices):
    STANDARD = "standard", "Standard VAT"
    ZERO_RATED = "zero_rated", "Zero-rated (basic food)"
    EXEMPT = "exempt", "Exempt"


class Product(BaseModel):
    """
    Core product. Food- and honey-specific data live in flexible JSON
    metadata fields (spec §10/§11) rather than hard-coded columns, so new
    product categories never require a migration.
    """

    name_en = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True)
    # Left blank, both are generated on save (apps.catalog.codes).
    sku = models.CharField(max_length=64, unique=True, blank=True, help_text="Leave blank to generate one.")
    barcode = models.CharField(max_length=64, blank=True, db_index=True, help_text="Leave blank to generate an internal EAN-13.")

    description = models.TextField(blank=True)
    short_description = models.CharField(max_length=500, blank=True)

    brand = models.ForeignKey("brands.Brand", on_delete=models.SET_NULL, null=True, blank=True, related_name="products")
    category = models.ForeignKey("categories.Category", on_delete=models.PROTECT, related_name="products")

    product_type = models.CharField(max_length=16, choices=ProductType.choices, default=ProductType.SIMPLE)
    status = models.CharField(max_length=16, choices=ProductStatus.choices, default=ProductStatus.DRAFT)
    published = models.BooleanField(default=False)
    featured = models.BooleanField(default=False)

    price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    compare_at_price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)
    cost_price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)
    tax_category = models.CharField(max_length=16, choices=TaxCategory.choices, default=TaxCategory.STANDARD)

    weight_grams = models.PositiveIntegerField(null=True, blank=True, help_text="Net weight in grams, used for weight-based shipping")
    packaging_type = models.CharField(max_length=16, choices=PackagingType.choices, blank=True)

    badges = ArrayField(models.CharField(max_length=20, choices=ProductBadgeCode.choices), default=list, blank=True)

    # Flexible, optional structured metadata — populated only when relevant.
    food_metadata = models.JSONField(default=dict, blank=True)
    honey_metadata = models.JSONField(default=dict, blank=True)

    low_stock_threshold = models.PositiveIntegerField(default=5)

    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=500, blank=True)

    attribute_values = models.ManyToManyField(
        "attributes.AttributeValue", through="ProductAttributeValue", related_name="products", blank=True
    )

    class Meta:
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["sku"]),
            models.Index(fields=["status", "published"]),
            models.Index(fields=["category"]),
            models.Index(fields=["brand"]),
        ]

    def __str__(self):
        return self.name_en

    def save(self, *args, **kwargs):
        codes.fill_codes(
            self, kind=codes.KIND_PRODUCT, make_sku=codes.product_sku,
            save_super=super().save, args=args, kwargs=kwargs,
        )

    @property
    def is_food(self) -> bool:
        return bool(self.food_metadata)

    @property
    def is_honey(self) -> bool:
        return bool(self.honey_metadata)


class ProductAttributeValue(BaseModel):
    """Descriptive (non-variant-defining) attribute assignment, e.g. Origin=Sinai."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="product_attribute_values")
    attribute_value = models.ForeignKey("attributes.AttributeValue", on_delete=models.CASCADE)

    class Meta:
        unique_together = [("product", "attribute_value")]


def product_image_upload_path(instance, filename):
    return f"products/{instance.product_id}/images/{filename}"


class ProductImage(BaseModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    variant = models.ForeignKey(
        "ProductVariant", on_delete=models.CASCADE, null=True, blank=True, related_name="images"
    )
    image = models.ImageField(upload_to=product_image_upload_path, validators=[validate_image_file])
    alt_text = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]


class VideoProvider(models.TextChoices):
    YOUTUBE = "youtube", "YouTube"
    VIMEO = "vimeo", "Vimeo"
    SELF_HOSTED = "self_hosted", "Self-hosted"


class ProductVideo(BaseModel):
    """Videos are referenced by URL — large binaries are not stored locally (spec §16)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="videos")
    video_url = models.URLField()
    thumbnail = models.ImageField(
        upload_to="products/videos/thumbnails/", null=True, blank=True, validators=[validate_image_file]
    )
    provider = models.CharField(max_length=16, choices=VideoProvider.choices, default=VideoProvider.SELF_HOSTED)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]


class ProductVariant(BaseModel):
    """A purchasable SKU, e.g. Sidr Honey 500g. Stock lives in apps.inventory."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    # Left blank, both are generated on save (apps.catalog.codes).
    sku = models.CharField(max_length=64, unique=True, blank=True, help_text="Leave blank to generate one.")
    barcode = models.CharField(max_length=64, blank=True, db_index=True, help_text="Leave blank to generate an internal EAN-13.")
    name_suffix = models.CharField(max_length=150, blank=True, help_text='e.g. "500g" — appended to the product name')

    price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP")
    compare_at_price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)
    cost_price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)
    weight_grams = models.PositiveIntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    attribute_values = models.ManyToManyField(
        "attributes.AttributeValue", through="VariantAttributeValue", related_name="variants", blank=True
    )

    class Meta:
        ordering = ["sort_order"]
        indexes = [models.Index(fields=["sku"]), models.Index(fields=["product", "is_active"])]

    def __str__(self):
        return f"{self.product.name_en} {self.name_suffix}".strip()

    def save(self, *args, **kwargs):
        codes.fill_codes(
            self, kind=codes.KIND_VARIANT, make_sku=codes.variant_sku,
            save_super=super().save, args=args, kwargs=kwargs,
        )


class VariantAttributeValue(BaseModel):
    """Variant-defining attribute assignment, e.g. Weight=500g. Unique per (variant, attribute)."""

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="variant_attribute_values")
    attribute_value = models.ForeignKey("attributes.AttributeValue", on_delete=models.CASCADE)

    class Meta:
        unique_together = [("variant", "attribute_value")]


class BundleItem(BaseModel):
    """Component of a Product with product_type=BUNDLE (spec §18)."""

    bundle = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="bundle_items", limit_choices_to={"product_type": ProductType.BUNDLE})
    component_variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="bundle_memberships")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]
        unique_together = [("bundle", "component_variant")]

    def __str__(self):
        return f"{self.quantity} x {self.component_variant} in {self.bundle}"


class MixMatchBox(BaseModel):
    """'Build Your Box' rules (spec §19) for a Product with product_type=MIX_AND_MATCH."""

    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name="mix_match_box", limit_choices_to={"product_type": ProductType.MIX_AND_MATCH}
    )
    minimum_items = models.PositiveIntegerField(default=1)
    maximum_items = models.PositiveIntegerField(default=1)
    fixed_price = MoneyField(max_digits=12, decimal_places=2, default_currency="EGP", null=True, blank=True)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    eligible_variants = models.ManyToManyField(ProductVariant, related_name="mix_match_boxes", blank=True)

    def __str__(self):
        return f"Mix & match: {self.product.name_en}"


class RecentlyViewed(BaseModel):
    """Tracks recently viewed products per user (or anonymous session). History
    is trimmed to MAX_HISTORY entries (spec §26)."""

    MAX_HISTORY = 20

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, null=True, blank=True, related_name="recently_viewed")
    session_key = models.CharField(max_length=40, null=True, blank=True, db_index=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="+")
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user", "viewed_at"]), models.Index(fields=["session_key", "viewed_at"])]
        # NOT unique_together on (user, session_key, product).
        #
        # Every row here has one of the two owner columns NULL — a guest has
        # no user, a signed-in customer has no session_key — and Postgres
        # treats NULL as distinct from NULL in a unique index. So the old
        # three-column constraint never fired for anybody, two concurrent
        # views of the same page inserted two rows, and update_or_create's
        # get() then raised MultipleObjectsReturned on every later view:
        # a permanent 500 on that product page for that visitor.
        #
        # Two partial constraints say what was actually meant, and each one
        # covers a case where neither column in it is NULL.
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                condition=models.Q(session_key__isnull=True),
                name="uniq_recentlyviewed_user_product",
            ),
            models.UniqueConstraint(
                fields=["session_key", "product"],
                condition=models.Q(user__isnull=True),
                name="uniq_recentlyviewed_session_product",
            ),
        ]

    @classmethod
    def trim_history(cls, *, user=None, session_key=None):
        qs = cls.objects.filter(user=user) if user else cls.objects.filter(session_key=session_key)
        stale_ids = list(qs.order_by("-viewed_at").values_list("id", flat=True)[cls.MAX_HISTORY :])
        if stale_ids:
            cls.objects.filter(id__in=stale_ids).delete()
