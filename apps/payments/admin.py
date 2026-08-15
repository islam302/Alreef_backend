from django.contrib import admin

from apps.payments.models import (
    Payment,
    PaymentAttempt,
    PaymentTransaction,
    PaymentWebhookEvent,
    Refund,
)


class PaymentTransactionInline(admin.TabularInline):
    model = PaymentTransaction
    extra = 0
    readonly_fields = ["transaction_type", "amount", "provider_transaction_id", "success", "created_at"]
    can_delete = False


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    readonly_fields = ["amount", "status", "provider_refund_id", "processed_at"]
    can_delete = False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "provider", "method", "status", "amount", "created_at"]
    list_filter = ["provider", "method", "status"]
    search_fields = ["order__order_number", "provider_reference"]
    inlines = [PaymentTransactionInline, RefundInline]


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ["payment", "attempt_number", "status", "created_at"]


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = ["provider", "event_type", "provider_event_id", "signature_valid", "processed", "created_at"]
    list_filter = ["provider", "processed", "signature_valid"]
    search_fields = ["provider_event_id"]


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ["id", "payment", "order", "amount", "status", "destination", "created_at"]
    list_filter = ["status", "destination"]
    search_fields = ["order__order_number"]
