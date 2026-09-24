from django.contrib import admin

from apps.analytics.models import AnalyticsEvent


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ["event_type", "user", "product", "order", "created_at"]
    list_filter = ["event_type"]
    search_fields = ["user__email"]
    date_hierarchy = "created_at"
