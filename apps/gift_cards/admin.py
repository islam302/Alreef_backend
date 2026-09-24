from django.contrib import admin

from apps.gift_cards.models import (
    GiftCard,
    GiftCardTransaction,
    StoreCreditAccount,
    StoreCreditTransaction,
)


class GiftCardTransactionInline(admin.TabularInline):
    model = GiftCardTransaction
    extra = 0
    readonly_fields = ["order", "amount", "balance_after", "created_at"]
    can_delete = False


@admin.register(GiftCard)
class GiftCardAdmin(admin.ModelAdmin):
    list_display = ["masked_code", "balance", "status", "purchaser", "expires_at", "created_at"]
    list_filter = ["status"]
    search_fields = ["code", "issued_to_email"]
    readonly_fields = ["code"]
    inlines = [GiftCardTransactionInline]

    @admin.display(description="Code")
    def masked_code(self, obj):
        return obj.masked_code


class StoreCreditTransactionInline(admin.TabularInline):
    model = StoreCreditTransaction
    extra = 0
    readonly_fields = ["amount", "reason", "order", "created_by", "created_at"]
    can_delete = False


@admin.register(StoreCreditAccount)
class StoreCreditAccountAdmin(admin.ModelAdmin):
    list_display = ["user", "balance"]
    search_fields = ["user__email"]
    inlines = [StoreCreditTransactionInline]
