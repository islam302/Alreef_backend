from django.db import models
from mptt.models import MPTTModel, TreeForeignKey

from apps.core.models import BaseModel
from apps.core.validators import validate_image_file


def category_image_upload_path(instance, filename):
    return f"categories/{instance.slug}/{filename}"


class Category(MPTTModel, BaseModel):
    """Hierarchical category tree (Honey / Food / Grocery / Beverages / ...)."""

    name_en = models.CharField(max_length=150)
    name_ar = models.CharField(max_length=150)
    slug = models.SlugField(max_length=180, unique=True)
    description = models.TextField(blank=True)
    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    image = models.ImageField(upload_to=category_image_upload_path, null=True, blank=True, validators=[validate_image_file])
    icon = models.CharField(max_length=64, blank=True, help_text="Optional icon identifier for frontend")

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=500, blank=True)

    class MPTTMeta:
        order_insertion_by = ["sort_order", "name_en"]

    class Meta:
        verbose_name_plural = "Categories"
        indexes = [models.Index(fields=["slug"]), models.Index(fields=["is_active"])]

    def __str__(self):
        return self.name_en
