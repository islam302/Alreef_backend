from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from djmoney.money import Money

from apps.checkout.services import tax_rate_for_product
from apps.core.error_codes import ApplicationError
from apps.inventory.services import reserve_stock
from apps.orders.models import Order, OrderItem
from apps.orders.services import generate_order_number
from apps.shipping.services import get_quote_for_method
from apps.subscriptions.models import (
    Subscription,
    SubscriptionSchedule,
    SubscriptionScheduleStatus,
    SubscriptionStatus,
)


@transaction.atomic
def pause_subscription(subscription: Subscription):
    subscription.status = SubscriptionStatus.PAUSED
    subscription.save(update_fields=["status"])
    return subscription


@transaction.atomic
def resume_subscription(subscription: Subscription):
    subscription.status = SubscriptionStatus.ACTIVE
    if subscription.next_order_date < timezone.now().date():
        subscription.next_order_date = timezone.now().date() + timedelta(days=subscription.interval_days)
    subscription.save(update_fields=["status", "next_order_date"])
    return subscription


@transaction.atomic
def cancel_subscription(subscription: Subscription):
    subscription.status = SubscriptionStatus.CANCELLED
    subscription.save(update_fields=["status"])
    return subscription


@transaction.atomic
def generate_order_for_subscription(subscription: Subscription) -> SubscriptionSchedule:
    schedule_entry = SubscriptionSchedule.objects.create(subscription=subscription, scheduled_date=subscription.next_order_date)

    items = list(subscription.items.select_related("variant__product").all())
    if not items:
        schedule_entry.status = SubscriptionScheduleStatus.SKIPPED
        schedule_entry.failure_reason = "Subscription has no items."
        schedule_entry.save(update_fields=["status", "failure_reason"])
        return schedule_entry

    currency = "EGP"
    try:
        subtotal = Money(0, currency)
        tax_total = Money(0, currency)
        line_data = []
        for item in items:
            unit_price = item.variant.price
            line_total = unit_price * item.quantity
            subtotal += line_total
            rate = tax_rate_for_product(item.variant.product)
            if rate > 0:
                tax_total += line_total * rate
            line_data.append((item, unit_price, line_total))

        quote = get_quote_for_method(governorate=subscription.address.governorate, method_id=subscription.shipping_method_id, subtotal=subtotal)
        grand_total = subtotal + tax_total + quote.price

        order = Order.objects.create(
            order_number=generate_order_number(), user=subscription.user, currency=currency,
            customer_name=subscription.address.full_name, customer_email=subscription.user.email,
            customer_phone=str(subscription.address.phone),
            shipping_address_snapshot={
                "governorate": subscription.address.governorate.name_en,
                "street": subscription.address.street, "building": subscription.address.building,
            },
            shipping_method_name=subscription.shipping_method.name, payment_method=subscription.payment_method,
            subtotal=subtotal, tax_total=tax_total, shipping_total=quote.price, grand_total=grand_total,
            notes=f"Auto-generated from subscription '{subscription.name}'",
        )

        for item, unit_price, line_total in line_data:
            reserve_stock(variant=item.variant, quantity=item.quantity, order=order)
            OrderItem.objects.create(
                order=order, variant=item.variant, product=item.variant.product,
                product_name_snapshot=item.variant.product.name_en, variant_name_snapshot=item.variant.name_suffix,
                sku_snapshot=item.variant.sku, unit_price=unit_price, quantity=item.quantity, line_total=line_total,
            )

        from apps.payments import config as payment_config
        from apps.payments.services import create_payment_for_order

        provider_code = payment_config.gateway_for_method(subscription.payment_method)
        payment = create_payment_for_order(
            order=order, provider_code=provider_code, method=subscription.payment_method,
            # One payment per scheduled delivery. If this task is retried after
            # the payment was created, the key finds the existing row instead of
            # billing the customer for the same delivery twice.
            idempotency_key=f"subscription:{subscription.id}:schedule:{schedule_entry.id}",
        )
        if subscription.payment_method == "cod":
            # Confirmed, not paid. The courier has not been yet — same rule as a
            # one-off COD order; the capture happens on delivery.
            from apps.checkout.services import confirm_order

            confirm_order(order, payment_received=False)
            order.refresh_from_db()

        schedule_entry.order = order
        schedule_entry.status = SubscriptionScheduleStatus.PROCESSED
        schedule_entry.save(update_fields=["order", "status"])

        from apps.subscriptions.models import SubscriptionPayment

        SubscriptionPayment.objects.create(subscription=subscription, schedule_entry=schedule_entry, payment=payment)

        subscription.last_order_date = subscription.next_order_date
        subscription.next_order_date = subscription.next_order_date + timedelta(days=subscription.interval_days)
        subscription.save(update_fields=["last_order_date", "next_order_date"])

    except ApplicationError as exc:
        schedule_entry.status = SubscriptionScheduleStatus.FAILED
        schedule_entry.failure_reason = exc.message
        schedule_entry.save(update_fields=["status", "failure_reason"])

    return schedule_entry


def process_due_subscriptions():
    today = timezone.now().date()
    due = Subscription.objects.filter(status=SubscriptionStatus.ACTIVE, next_order_date__lte=today)
    return [generate_order_for_subscription(s) for s in due]
