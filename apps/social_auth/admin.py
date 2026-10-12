from django.contrib import admin

from apps.social_auth.models import SocialAccount


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ["user", "provider", "email", "created_at"]
    list_filter = ["provider"]
    search_fields = ["user__email", "email", "uid"]
    raw_id_fields = ["user"]
    readonly_fields = ["provider", "uid", "email", "created_at"]
