from django.contrib import admin

from apps.inventory.models import Batch, InventoryItem, InventoryMovement, StockReservation


class BatchInline(admin.TabularInline):
    model = Batch
    extra = 0
    fields = ["batch_number", "production_date", "expiration_date", "initial_quantity", "quantity", "cost_price", "is_active"]


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ["variant", "quantity_on_hand", "reserved_quantity", "available_quantity", "is_low_stock", "is_out_of_stock", "track_inventory"]
    search_fields = ["variant__sku", "variant__product__name_en"]
    inlines = [BatchInline]

    @admin.display(boolean=True)
    def is_low_stock(self, obj):
        return obj.is_low_stock

    @admin.display(boolean=True)
    def is_out_of_stock(self, obj):
        return obj.is_out_of_stock


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ["batch_number", "item", "expiration_date", "quantity", "initial_quantity", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["batch_number", "item__variant__sku"]
    date_hierarchy = "expiration_date"


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = ["item", "movement_type", "quantity_delta", "reference", "created_at"]
    list_filter = ["movement_type"]
    search_fields = ["reference", "item__variant__sku"]


@admin.register(StockReservation)
class StockReservationAdmin(admin.ModelAdmin):
    list_display = ["item", "quantity", "status", "cart_id", "order", "expires_at"]
    list_filter = ["status"]
