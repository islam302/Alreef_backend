from django.db import models

from apps.core.models import BaseModel
from apps.core.validators import validate_image_file


def brand_logo_upload_path(instance, filename):
    return f"brands/{instance.slug}/{filename}"


class Brand(BaseModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=180, unique=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to=brand_logo_upload_path, null=True, blank=True, validators=[validate_image_file])
    website = models.URLField(blank=True)
    country_of_origin = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["slug"])]

    def __str__(self):
        return self.name
