from celery import shared_task


@shared_task
def record_event_task(*, event_type: str, user_id=None, session_key: str = "", product_id=None, order_id=None, metadata=None):
    from apps.analytics.models import AnalyticsEvent

    AnalyticsEvent.objects.create(
        event_type=event_type, user_id=user_id, session_key=session_key, product_id=product_id, order_id=order_id,
        metadata=metadata or {},
    )
