from django.contrib import admin

from apps.carts.models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "session_key", "status", "coupon_code", "last_activity_at"]
    list_filter = ["status"]
    search_fields = ["user__email", "session_key"]
    inlines = [CartItemInline]
