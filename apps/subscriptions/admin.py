from django.contrib import admin

from apps.subscriptions.models import (
    Subscription,
    SubscriptionItem,
    SubscriptionPayment,
    SubscriptionSchedule,
)


class SubscriptionItemInline(admin.TabularInline):
    model = SubscriptionItem
    extra = 0


class SubscriptionScheduleInline(admin.TabularInline):
    model = SubscriptionSchedule
    extra = 0
    readonly_fields = ["scheduled_date", "status", "order", "failure_reason"]
    can_delete = False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "frequency", "status", "next_order_date"]
    list_filter = ["status", "frequency"]
    search_fields = ["name", "user__email"]
    inlines = [SubscriptionItemInline, SubscriptionScheduleInline]


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ["subscription", "schedule_entry", "payment"]
