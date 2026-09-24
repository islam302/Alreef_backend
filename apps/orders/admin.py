from django.contrib import admin

from apps.orders.models import (
    Order,
    OrderItem,
    OrderStatusHistory,
    ReturnItem,
    ReturnPolicySettings,
    ReturnRequest,
)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["product", "variant", "product_name_snapshot", "sku_snapshot", "unit_price", "quantity", "line_total"]
    can_delete = False


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ["from_status", "to_status", "changed_by", "note", "created_at"]
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "user", "status", "payment_method", "grand_total", "created_at"]
    list_filter = ["status", "payment_method"]
    search_fields = ["order_number", "customer_email", "customer_phone", "user__email"]
    autocomplete_fields = ["user"]
    inlines = [OrderItemInline, OrderStatusHistoryInline]
    readonly_fields = ["order_number", "subtotal", "grand_total"]


class ReturnItemInline(admin.TabularInline):
    model = ReturnItem
    extra = 0


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "user", "reason", "status", "created_at"]
    list_filter = ["status", "reason"]
    search_fields = ["order__order_number", "user__email"]
    inlines = [ReturnItemInline]
    actions = ["approve_returns", "reject_returns", "complete_and_refund"]

    @admin.action(description="Approve selected return requests")
    def approve_returns(self, request, queryset):
        from apps.orders.services import decide_return_request

        for return_request in queryset:
            decide_return_request(return_request=return_request, approve=True, decided_by=request.user)

    @admin.action(description="Reject selected return requests")
    def reject_returns(self, request, queryset):
        from apps.orders.services import decide_return_request

        for return_request in queryset:
            decide_return_request(return_request=return_request, approve=False, decided_by=request.user)

    @admin.action(description="Mark item received & issue refund")
    def complete_and_refund(self, request, queryset):
        from apps.orders.services import complete_return_and_refund

        for return_request in queryset:
            complete_return_and_refund(return_request=return_request, processed_by=request.user)


@admin.register(ReturnPolicySettings)
class ReturnPolicySettingsAdmin(admin.ModelAdmin):
    list_display = ["return_window_days", "allow_change_of_mind_returns", "require_approval"]

    def has_add_permission(self, request):
        return not ReturnPolicySettings.objects.exists()
