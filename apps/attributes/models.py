from django.db import models

from apps.core.models import BaseModel


class AttributeInputType(models.TextChoices):
    SELECT = "select", "Select (single choice)"
    MULTISELECT = "multiselect", "Multi-select"
    TEXT = "text", "Free text"
    NUMBER = "number", "Number"
    BOOLEAN = "boolean", "Boolean"


class Attribute(BaseModel):
    """
    A product property such as Size, Weight, Color, Flavor, Origin, Packaging.
    New attributes/values are created via admin/API data, never migrations
    (spec §13).
    """

    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    code = models.SlugField(max_length=100, unique=True)
    input_type = models.CharField(max_length=16, choices=AttributeInputType.choices, default=AttributeInputType.SELECT)
    is_variant_attribute = models.BooleanField(
        default=False, help_text="If true, this attribute defines distinct purchasable variants (e.g. Weight)."
    )
    is_filterable = models.BooleanField(default=True, help_text="Show in product search filters.")
    unit = models.CharField(max_length=20, blank=True, help_text="e.g. g, kg, ml")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name_en"]

    def __str__(self):
        return self.name_en


class AttributeValue(BaseModel):
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="values")
    value_en = models.CharField(max_length=150)
    value_ar = models.CharField(max_length=150)
    slug = models.SlugField(max_length=180)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "value_en"]
        unique_together = [("attribute", "slug")]

    def __str__(self):
        return f"{self.attribute.name_en}: {self.value_en}"
