import pytest

from apps.notifications.models import Notification, NotificationEvent, NotificationLog
from apps.notifications.services import log_delivery
from apps.users.models import User

pytestmark = pytest.mark.django_db

NOTIFICATIONS_URL = "/api/v1/notifications/"
PREFERENCES_URL = "/api/v1/notifications/preferences/"


def _mark_read_url(pk):
    return f"/api/v1/notifications/{pk}/read/"


def test_notification_list_requires_authentication(api_client):
    response = api_client.get(NOTIFICATIONS_URL)

    assert response.status_code == 401


def test_notification_list_scoped_to_requesting_user(authenticated_client, user):
    other_user = User.objects.create_user(email="other-notif@example.com", password="SuperSecret123")
    Notification.objects.create(user=user, event=NotificationEvent.ORDER_CREATED, title="Your order was placed")
    Notification.objects.create(user=other_user, event=NotificationEvent.ORDER_CREATED, title="Someone else's order")

    response = authenticated_client.get(NOTIFICATIONS_URL)

    assert response.status_code == 200
    titles = [n["title"] for n in response.json()["data"]["results"]]
    assert titles == ["Your order was placed"]


def test_mark_notification_read_success(authenticated_client, user):
    notification = Notification.objects.create(user=user, event=NotificationEvent.ORDER_DELIVERED, title="Delivered")

    response = authenticated_client.post(_mark_read_url(notification.id))

    assert response.status_code == 200
    assert response.json()["data"]["read_at"] is not None
    notification.refresh_from_db()
    assert notification.read_at is not None


def test_mark_notification_read_is_idempotent(authenticated_client, user):
    notification = Notification.objects.create(user=user, event=NotificationEvent.ORDER_DELIVERED, title="Delivered")

    authenticated_client.post(_mark_read_url(notification.id))
    first_read_at = Notification.objects.get(pk=notification.id).read_at
    authenticated_client.post(_mark_read_url(notification.id))
    second_read_at = Notification.objects.get(pk=notification.id).read_at

    assert first_read_at == second_read_at


def test_mark_read_on_other_users_notification_returns_404(authenticated_client, user):
    other_user = User.objects.create_user(email="owner@example.com", password="SuperSecret123")
    notification = Notification.objects.create(user=other_user, event=NotificationEvent.ORDER_CREATED, title="Not yours")

    response = authenticated_client.post(_mark_read_url(notification.id))

    assert response.status_code == 404
    notification.refresh_from_db()
    assert notification.read_at is None


def test_notification_preferences_get_defaults(authenticated_client):
    response = authenticated_client.get(PREFERENCES_URL)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["marketing_email"] is True
    assert data["marketing_sms"] is False


def test_notification_preferences_patch_updates_values(authenticated_client, user):
    response = authenticated_client.patch(PREFERENCES_URL, {"marketing_sms": True, "marketing_email": False}, format="json")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["marketing_sms"] is True
    assert data["marketing_email"] is False


def test_notification_preferences_requires_authentication(api_client):
    response = api_client.get(PREFERENCES_URL)

    assert response.status_code == 401


def test_log_delivery_accepts_user_id_without_typeerror(user):
    """Regression guard: apps.notifications.tasks.send_email_task etc. call
    log_delivery(..., user_id=...), not log_delivery(..., user=...). This
    previously raised a TypeError because the signature only accepted
    `user=None`. See config/settings/testing.py's note on the bug."""
    log_delivery(
        event=NotificationEvent.ORDER_CREATED, channel="email", recipient=user.email, success=True, user_id=user.id,
    )

    log = NotificationLog.objects.get(recipient=user.email)
    assert log.user_id == user.id
    assert log.success is True
    assert log.event == NotificationEvent.ORDER_CREATED
