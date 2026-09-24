from django.contrib import admin

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["action", "model_name", "object_id", "actor", "method", "path", "ip_address", "created_at"]
    list_filter = ["action", "model_name"]
    search_fields = ["actor__email", "object_id", "path"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Audit rows must be immutable — no add, no edit, no delete — so a
        # compromised staff account can't cover its tracks.
        return False
