from django.contrib import admin

from apps.promotions.models import Promotion


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ["name", "promotion_type", "is_active", "stackable", "priority", "start_date", "end_date"]
    list_filter = ["promotion_type", "is_active", "stackable"]
    search_fields = ["name", "description"]
    filter_horizontal = ["products", "categories", "brands"]
