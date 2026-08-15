from django.contrib import admin

from apps.coupons.models import Coupon, CouponUsage


class CouponUsageInline(admin.TabularInline):
    model = CouponUsage
    extra = 0
    readonly_fields = ["user", "order", "discount_amount", "used_at"]
    can_delete = False


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ["code", "discount_type", "is_active", "times_used", "usage_limit", "start_date", "expiration_date"]
    list_filter = ["discount_type", "is_active"]
    search_fields = ["code", "description"]
    filter_horizontal = ["products", "categories", "brands"]
    inlines = [CouponUsageInline]
