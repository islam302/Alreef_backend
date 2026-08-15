from django.contrib import admin

from apps.loyalty.models import (
    LoyaltyAccount,
    LoyaltyProgramSettings,
    LoyaltyTier,
    LoyaltyTransaction,
)


@admin.register(LoyaltyProgramSettings)
class LoyaltyProgramSettingsAdmin(admin.ModelAdmin):
    list_display = ["points_per_amount", "amount_unit", "redemption_value_per_point", "points_expiry_days"]

    def has_add_permission(self, request):
        return not LoyaltyProgramSettings.objects.exists()


@admin.register(LoyaltyTier)
class LoyaltyTierAdmin(admin.ModelAdmin):
    list_display = ["name", "minimum_points", "sort_order"]


class LoyaltyTransactionInline(admin.TabularInline):
    model = LoyaltyTransaction
    extra = 0
    readonly_fields = ["transaction_type", "points", "order", "note", "created_at"]
    can_delete = False


@admin.register(LoyaltyAccount)
class LoyaltyAccountAdmin(admin.ModelAdmin):
    list_display = ["user", "points_balance", "lifetime_points_earned"]
    search_fields = ["user__email"]
    inlines = [LoyaltyTransactionInline]
