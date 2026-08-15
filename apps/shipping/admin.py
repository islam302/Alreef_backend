from django.contrib import admin

from apps.shipping.models import (
    City,
    Governorate,
    Shipment,
    ShipmentEvent,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
)


@admin.register(Governorate)
class GovernorateAdmin(admin.ModelAdmin):
    list_display = ["name_en", "name_ar", "code", "is_active", "sort_order"]
    search_fields = ["name_en", "name_ar", "code"]
    list_editable = ["is_active", "sort_order"]


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ["name_en", "governorate", "is_active"]
    list_filter = ["governorate"]
    search_fields = ["name_en", "name_ar"]


class ShippingRateInline(admin.TabularInline):
    model = ShippingRate
    extra = 1


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]
    filter_horizontal = ["governorates"]
    inlines = [ShippingRateInline]


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = ["name", "method_type", "carrier_code", "min_delivery_days", "max_delivery_days", "is_active"]
    list_editable = ["is_active"]


class ShipmentEventInline(admin.TabularInline):
    model = ShipmentEvent
    extra = 0


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "tracking_number", "status", "carrier_code", "shipped_at", "delivered_at"]
    list_filter = ["status", "carrier_code"]
    search_fields = ["tracking_number", "order__order_number"]
    inlines = [ShipmentEventInline]
