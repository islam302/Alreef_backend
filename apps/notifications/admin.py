from django.contrib import admin

from apps.notifications.models import Notification, NotificationLog, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "event", "title", "read_at", "created_at"]
    list_filter = ["event", "read_at"]
    search_fields = ["user__email", "title"]
    autocomplete_fields = ["user"]


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ["id", "event", "channel", "recipient", "success", "created_at"]
    list_filter = ["channel", "success", "event"]
    search_fields = ["recipient"]


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ["user", "marketing_email", "marketing_sms", "marketing_whatsapp", "marketing_push"]
    autocomplete_fields = ["user"]
