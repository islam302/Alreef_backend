from django.contrib import admin

from apps.brands.models import Brand


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "country_of_origin", "is_active"]
    list_filter = ["is_active", "country_of_origin"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
