from datetime import timedelta

from django.db.models import Avg, Count, Sum
from django.utils import timezone


def track(*, event_type: str, request=None, user=None, product_id=None, order_id=None, metadata=None):
    from apps.analytics.tasks import record_event_task

    session_key = ""
    if request is not None:
        if not request.session.session_key:
            request.session.save()
        session_key = request.session.session_key or ""
        user = user or (request.user if getattr(request, "user", None) and request.user.is_authenticated else None)

    record_event_task.delay(
        event_type=event_type, user_id=user.id if user else None, session_key=session_key,
        product_id=product_id, order_id=order_id, metadata=metadata or {},
    )


def get_admin_dashboard_summary() -> dict:
    """Powers the admin dashboard (spec §53)."""
    from apps.inventory.models import InventoryItem
    from apps.inventory.services import get_expired_batches
    from apps.orders.models import Order, OrderStatus
    from apps.payments.models import Payment, PaymentStatus
    from apps.users.models import User

    today = timezone.now().date()
    today_orders = Order.objects.filter(created_at__date=today)
    all_orders_excl_cancelled = Order.objects.exclude(status=OrderStatus.CANCELLED)

    revenue_today = today_orders.exclude(status=OrderStatus.CANCELLED).aggregate(total=Sum("grand_total"))["total"] or 0
    aov = all_orders_excl_cancelled.aggregate(avg=Avg("grand_total"))["avg"] or 0

    top_products = (
        Order.objects.exclude(status=OrderStatus.CANCELLED)
        .values("items__product__name_en")
        .annotate(units_sold=Sum("items__quantity"))
        .order_by("-units_sold")[:5]
    )

    # One COUNT over annotated totals. This used to load every inventory row
    # and ask each one in Python, which cost two sum queries per item — 36 of
    # the overview's queries on a six-product catalogue, and growing with the
    # catalogue on the one panel staff see first.
    low_stock_count = InventoryItem.objects.low_stock().count()
    expired_batches_count = get_expired_batches().count()

    payments_today = Payment.objects.filter(created_at__date=today)
    payment_success_rate = 0
    if payments_today.exists():
        captured = payments_today.filter(status=PaymentStatus.CAPTURED).count()
        payment_success_rate = round(captured / payments_today.count() * 100, 1)

    return {
        "today_sales": str(revenue_today),
        "today_order_count": today_orders.count(),
        "average_order_value": str(aov),
        "top_products": list(top_products),
        "low_stock_count": low_stock_count,
        "expired_batches_count": expired_batches_count,
        "pending_orders": Order.objects.filter(status=OrderStatus.PENDING).count(),
        "failed_payments_today": payments_today.filter(status=PaymentStatus.FAILED).count(),
        "payment_success_rate": payment_success_rate,
        "cod_orders_pending": Order.objects.filter(payment_method="cod", status=OrderStatus.PENDING).count(),
        "refunds_today": Payment.objects.filter(refunds__created_at__date=today).count(),
        "total_customers": User.objects.filter(is_staff=False).count(),
        "new_customers_today": User.objects.filter(is_staff=False, date_joined__date=today).count(),
    }
