"""
Celery tasks (run eagerly / directly), notification providers, loyalty point
expiry, shipping carrier registry and the small core helpers.
"""
import datetime
import io
import logging
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from apps.notifications.models import Notification, NotificationLog

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# notifications.tasks
# --------------------------------------------------------------------------


def test_send_email_task_success_is_logged(user, mailoutbox):
    from apps.notifications.tasks import send_email_task

    result = send_email_task(to=user.email, subject="Hi", template_name="welcome", context={}, event="welcome", user_id=user.id)
    assert result["success"] is True
    assert len(mailoutbox) == 1
    # No hand-written .txt: the plain part is derived from the HTML, never raw tags.
    assert "<html" not in mailoutbox[0].body.lower()
    assert NotificationLog.objects.filter(recipient=user.email, success=True).exists()


def test_send_email_task_failure_is_logged_and_retried(user, monkeypatch):
    from django.core.mail import EmailMultiAlternatives

    from apps.notifications.tasks import send_email_task

    def fail(self, fail_silently=False):
        raise ConnectionError("smtp down")

    monkeypatch.setattr(EmailMultiAlternatives, "send", fail)
    with pytest.raises(Exception):
        send_email_task(to=user.email, subject="Hi", template_name="welcome", context={}, event="welcome")
    log = NotificationLog.objects.get(recipient=user.email)
    assert log.success is False and "smtp down" in log.error_message


def test_sms_and_whatsapp_tasks(user):
    from apps.notifications.tasks import send_sms_task, send_whatsapp_task

    assert send_sms_task(to="+201001234567", message="hello", event="welcome")["success"] is True
    assert send_whatsapp_task(to="+201001234567", template_name="t", params={}, event="welcome")["success"] is True
    assert NotificationLog.objects.count() == 2


@pytest.mark.parametrize("task_name,kwargs", [
    ("send_sms_task", {"to": "+20100", "message": "x"}),
    ("send_whatsapp_task", {"to": "+20100", "template_name": "t", "params": {}}),
])
def test_sms_and_whatsapp_failures_retry(monkeypatch, task_name, kwargs):
    from apps.notifications import tasks

    monkeypatch.setattr(tasks, "send_sms_now", lambda **k: {"success": False})
    monkeypatch.setattr(tasks, "send_whatsapp_now", lambda **k: {"success": False})
    with pytest.raises(Exception):
        getattr(tasks, task_name)(event="welcome", **kwargs)
    assert NotificationLog.objects.filter(success=False).count() == 1


def test_in_app_notification_task(user):
    from apps.notifications.tasks import create_in_app_notification_task

    nid = create_in_app_notification_task(user_id=user.id, event="welcome", title="أهلاً")
    assert Notification.objects.get(pk=nid).user == user
    assert create_in_app_notification_task(user_id=999999, event="welcome", title="x") is None


def test_low_stock_and_expiry_alert_tasks(product_with_variant_and_stock, settings, monkeypatch):
    from apps.notifications import tasks

    lines = []
    monkeypatch.setattr(tasks.logger, "info", lambda msg, *args: lines.append(msg % args))
    batch = product_with_variant_and_stock["batch"]
    batch.expiration_date = datetime.date.today() + timedelta(days=7)
    batch.save()
    settings.EXPIRATION_ALERT_DAYS = [7, 14]
    tasks.notify_low_stock_task(product_with_variant_and_stock["variant"].id, 1)
    tasks.notify_expiration_alerts_task()
    assert any("BATCH-001" in line and "7d" in line for line in lines)
    assert len(lines) == 2


def test_abandoned_cart_task_notifies_once(authenticated_client, user, product_with_variant_and_stock, mailoutbox, settings):
    from apps.carts.models import Cart
    from apps.notifications.tasks import process_abandoned_carts_task

    variant = product_with_variant_and_stock["variant"]
    authenticated_client.post("/api/v1/cart/items/", {"variant_id": variant.id, "quantity": 1}, format="json")
    Cart.objects.filter(user=user).update(last_activity_at=timezone.now() - timedelta(hours=settings.ABANDONED_CART_HOURS + 1))

    process_abandoned_carts_task()
    process_abandoned_carts_task()

    assert len(mailoutbox) == 1
    assert Cart.objects.get(user=user).abandoned_notified_at is not None


# --------------------------------------------------------------------------
# notification providers
# --------------------------------------------------------------------------


def test_unknown_message_providers_are_refused(settings):
    from apps.notifications.providers import get_sms_provider, get_whatsapp_provider, sms_reaches_customers

    settings.SMS_PROVIDER = "twilio"
    settings.WHATSAPP_PROVIDER = "meta"
    assert sms_reaches_customers() is True
    with pytest.raises(NotImplementedError):
        get_sms_provider()
    with pytest.raises(NotImplementedError):
        get_whatsapp_provider()


def test_console_sms_is_not_delivery_in_production(settings):
    from apps.notifications.providers import sms_reaches_customers

    settings.SMS_PROVIDER = "console"
    settings.SMS_CONSOLE_IS_DELIVERY = False
    assert sms_reaches_customers() is False


def test_queue_email_without_recipient_does_nothing(mailoutbox):
    from apps.notifications.services import queue_email

    queue_email(to="", subject="x", template_name="welcome", context={})
    assert mailoutbox == []


def test_queue_email_survives_a_broker_outage(monkeypatch):
    from apps.notifications import services
    from apps.notifications.tasks import send_email_task

    def down(**kw):
        raise ConnectionError("redis down")

    monkeypatch.setattr(send_email_task, "delay", down)
    services._dispatch_email({"to": "a@b.c", "template_name": "welcome"})  # must not raise


def test_subject_falls_back_for_unknown_template():
    from apps.notifications.services import subject_for

    assert subject_for("nope", "Fallback", {}) == "Fallback"


# --------------------------------------------------------------------------
# inventory / loyalty / subscriptions / payments beat tasks
# --------------------------------------------------------------------------


def test_deactivate_expired_batches_task(product_with_variant_and_stock):
    from apps.inventory.models import InventoryMovement, InventoryMovementType
    from apps.inventory.tasks import deactivate_expired_batches_task, release_expired_reservations_task

    batch = product_with_variant_and_stock["batch"]
    batch.expiration_date = datetime.date.today() - timedelta(days=1)
    batch.save()
    assert deactivate_expired_batches_task() == 1
    batch.refresh_from_db()
    assert batch.is_active is False
    assert InventoryMovement.objects.filter(batch=batch, movement_type=InventoryMovementType.EXPIRED).exists()
    assert deactivate_expired_batches_task() == 0
    assert release_expired_reservations_task() == 0


def _earn(account, points, expires_at):
    from apps.loyalty.models import LoyaltyTransaction, LoyaltyTransactionType

    account.points_balance += points
    account.save()
    return LoyaltyTransaction.objects.create(
        account=account, transaction_type=LoyaltyTransactionType.EARN, points=points, expires_at=expires_at,
    )


def test_expire_points_task_runs_and_is_idempotent(user):
    from apps.loyalty.services import get_or_create_account
    from apps.loyalty.tasks import expire_stale_points_task

    account = get_or_create_account(user)
    _earn(account, 100, timezone.now() - timedelta(days=1))
    _earn(account, 50, timezone.now() + timedelta(days=30))

    assert expire_stale_points_task() == 1
    account.refresh_from_db()
    assert account.points_balance == 50
    assert expire_stale_points_task() == 0
    account.refresh_from_db()
    assert account.points_balance == 50


def test_points_earned_after_a_first_expiry_still_expire(user):
    """
    Once an account had one EXPIRE row, none of its later earnings could ever
    expire: the query excluded every earn on any account holding an EXPIRE.
    """
    from apps.loyalty.models import LoyaltyTransaction
    from apps.loyalty.services import expire_stale_points, get_or_create_account

    account = get_or_create_account(user)
    _earn(account, 100, timezone.now() - timedelta(days=10))
    assert expire_stale_points() == 1
    account.refresh_from_db()
    assert account.points_balance == 0

    later = _earn(account, 40, timezone.now() + timedelta(days=5))
    LoyaltyTransaction.objects.filter(pk=later.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert expire_stale_points() == 1
    account.refresh_from_db()
    assert account.points_balance == 0


def test_expiry_skips_an_empty_balance(user):
    from apps.loyalty.services import expire_stale_points, get_or_create_account

    account = get_or_create_account(user)
    _earn(account, 10, timezone.now() - timedelta(days=1))
    account.points_balance = 0
    account.save()
    assert expire_stale_points() == 0


def test_process_due_subscriptions_task(user, address, shipping_setup, product_with_variant_and_stock):
    from apps.subscriptions.models import Subscription, SubscriptionItem, SubscriptionStatus
    from apps.subscriptions.tasks import process_due_subscriptions_task

    sub = Subscription.objects.create(
        user=user, name="Box", address=address, shipping_method=shipping_setup["method"], payment_method="cod",
        frequency="monthly", next_order_date=datetime.date.today(),
    )
    SubscriptionItem.objects.create(subscription=sub, variant=product_with_variant_and_stock["variant"], quantity=1)
    Subscription.objects.create(
        user=user, name="Paused", address=address, shipping_method=shipping_setup["method"], payment_method="cod",
        frequency="monthly", next_order_date=datetime.date.today(), status=SubscriptionStatus.PAUSED,
    )
    assert process_due_subscriptions_task() == 1
    sub.refresh_from_db()
    assert sub.next_order_date > datetime.date.today()
    assert process_due_subscriptions_task() == 0


# --------------------------------------------------------------------------
# shipping carriers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["bosta", "aramex"])
def test_carriers_refuse_until_enabled(code):
    from apps.shipping.providers import get_shipping_provider

    with pytest.raises(NotImplementedError):
        get_shipping_provider(code)


@pytest.mark.parametrize("code,key", [("bosta", "BOSTA"), ("aramex", "ARAMEX")])
def test_enabled_carrier_skeletons_never_pretend(settings, code, key):
    from apps.shipping.providers import get_shipping_provider

    settings.SHIPPING_PROVIDERS = {**settings.SHIPPING_PROVIDERS, key: {**settings.SHIPPING_PROVIDERS[key], "ENABLED": True}}
    provider = get_shipping_provider(code)
    for call in (lambda: provider.create_shipment(shipment=None, order=None),
                 lambda: provider.cancel_shipment(shipment=None),
                 lambda: provider.get_tracking(shipment=None)):
        with pytest.raises(NotImplementedError):
            call()


def test_manual_carrier_and_unknown_code():
    from apps.shipping.providers import get_shipping_provider

    manual = get_shipping_provider("")
    assert manual.create_shipment(shipment=None, order=None).success is True
    assert manual.cancel_shipment(shipment=None) is True
    assert manual.get_tracking(shipment=None) == []
    with pytest.raises(NotImplementedError):
        get_shipping_provider("dhl")


# --------------------------------------------------------------------------
# core helpers
# --------------------------------------------------------------------------


def test_permissions():
    from apps.core.permissions import IsOwner, IsOwnerOrStaff, IsStaffOrReadOnly, ReadOnly

    owner = SimpleNamespace(is_staff=False)
    staff = SimpleNamespace(is_staff=True)
    obj = SimpleNamespace(user=owner)
    get = SimpleNamespace(method="GET", user=owner)
    post = SimpleNamespace(method="POST", user=owner)

    assert IsOwner().has_object_permission(get, None, obj)
    assert not IsOwner().has_object_permission(SimpleNamespace(user=staff), None, obj)
    assert IsOwnerOrStaff().has_object_permission(SimpleNamespace(user=staff), None, obj)
    assert not IsOwnerOrStaff().has_object_permission(SimpleNamespace(user=SimpleNamespace(is_staff=False)), None, SimpleNamespace())
    assert ReadOnly().has_permission(get, None) and not ReadOnly().has_permission(post, None)
    assert IsStaffOrReadOnly().has_permission(get, None)
    assert not IsStaffOrReadOnly().has_permission(post, None)
    assert IsStaffOrReadOnly().has_permission(SimpleNamespace(method="POST", user=staff), None)


def _image(name="a.png", size=(10, 10), fmt="PNG", content_type="image/png"):
    buf = io.BytesIO()
    Image.new("RGB", size).save(buf, format=fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=content_type)


def test_image_validator():
    from apps.core import validators

    validators.validate_image_file(_image())
    with pytest.raises(ValidationError):
        validators.validate_image_file(_image(name="a.gif"))
    with pytest.raises(ValidationError):
        validators.validate_image_file(_image(content_type="image/gif"))
    with pytest.raises(ValidationError):
        validators.validate_image_file(SimpleUploadedFile("a.png", b"not an image", content_type="image/png"))
    big = SimpleUploadedFile("a.png", b"x" * (validators.MAX_IMAGE_SIZE_BYTES + 1), content_type="image/png")
    with pytest.raises(ValidationError):
        validators.validate_image_file(big)


def test_image_validator_rejects_huge_dimensions(monkeypatch):
    from apps.core import validators

    monkeypatch.setattr(validators, "MAX_IMAGE_DIMENSION", 5)
    with pytest.raises(ValidationError):
        validators.validate_image_file(_image(size=(10, 10)))


def test_video_and_html_validators():
    from apps.core.validators import validate_no_unsafe_html, validate_video_file

    validate_video_file(SimpleNamespace(name="clip.mp4"))
    with pytest.raises(ValidationError):
        validate_video_file(SimpleNamespace(name="clip.exe"))
    validate_no_unsafe_html("")
    validate_no_unsafe_html("عسل **طبيعي**")
    for bad in ("<script>x</script>", "javascript:alert(1)", '<img onerror="x">'):
        with pytest.raises(ValidationError):
            validate_no_unsafe_html(bad)


def test_logging_filter_and_json_formatter():
    import json

    from apps.core.logging_filters import JsonFormatter, SensitiveDataFilter

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "password=%(p)s", None, None)
    record.args = {"p": "secret", "password": "hunter2"}
    SensitiveDataFilter().filter(record)
    assert record.args["password"] == "***REDACTED***"
    assert "password" not in record.msg

    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    rec.request_id = "req-1"
    payload = json.loads(JsonFormatter().format(rec))
    assert payload["request_id"] == "req-1" and "boom" in payload["exc_info"]


def test_user_manager_rules():
    from apps.users.models import User

    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="x")
    admin = User.objects.create_superuser(email="Boss@Example.COM", password="SuperSecret123")
    assert admin.is_staff and admin.is_superuser and admin.email_verified
    assert admin.email == "Boss@example.com"
    with pytest.raises(ValueError):
        User.objects.create_superuser(email="a@b.c", password="x", is_staff=False)
    with pytest.raises(ValueError):
        User.objects.create_superuser(email="a@b.c", password="x", is_superuser=False)
