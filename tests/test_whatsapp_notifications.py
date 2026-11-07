"""
Order updates on WhatsApp, through Meta's official WhatsApp Cloud API.

No test here touches the network: the Cloud API is `requests.post`, mocked.

* The provider builds the template request Meta documents and reads the
  message id back; it tells a failure worth retrying from one that is not.
* Numbers are sent as WhatsApp addresses them, whatever the customer typed.
* An order — signed-in or guest, COD or transfer — queues its message after
  commit, and only when a template for it is configured.
* Every send is recorded in NotificationLog, success or failure.
"""
import uuid
from unittest import mock

import pytest
import requests
from django.db import transaction
from rest_framework.test import APIClient

from apps.notifications.models import NotificationChannel, NotificationLog
from apps.orders.models import Order, OrderStatus
from tests.conftest import INSTAPAY_TEST_CONFIG, payment_providers_with

pytestmark = pytest.mark.django_db

CHECKOUT_URL = "/api/v1/checkout/"
CART_ITEMS_URL = "/api/v1/cart/items/"
DELAY = "apps.notifications.tasks.send_whatsapp_task.delay"
POST = "apps.notifications.providers.meta_cloud.requests.post"


def _templates(**names):
    """WHATSAPP_ORDER_TEMPLATES with `names` switched on, default parameters kept."""
    from django.conf import settings

    configured = {key: {**value} for key, value in settings.WHATSAPP_ORDER_TEMPLATES.items()}
    for message, name in names.items():
        configured[message]["name"] = name
    return configured


@pytest.fixture
def whatsapp_on(settings):
    settings.WHATSAPP_ORDER_TEMPLATES = _templates(
        order_awaiting_payment="elreef_awaiting_payment",
        order_confirmed="elreef_order_confirmed",
        order_shipped="elreef_order_shipped",
        order_out_for_delivery="elreef_out_for_delivery",
        order_delivered="elreef_order_delivered",
        order_cancelled="elreef_order_cancelled",
    )
    return settings


@pytest.fixture
def meta(settings):
    settings.WHATSAPP_PROVIDER = "meta_cloud_api"
    settings.WHATSAPP_PHONE_NUMBER_ID = "123456789012345"
    settings.WHATSAPP_ACCESS_TOKEN = "test-token"
    settings.WHATSAPP_GRAPH_API_VERSION = "v24.0"
    settings.WHATSAPP_TEMPLATE_LANGUAGE = "ar"
    return settings


def _response(status, body):
    response = mock.Mock(status_code=status)
    response.json.return_value = body
    return response


ACCEPTED = {
    "messaging_product": "whatsapp",
    "contacts": [{"input": "201001234567", "wa_id": "201001234567"}],
    "messages": [{"id": "wamid.HBgMMjAxMDAxMjM0NTY3FQIAERgSQ"}],
}


def _meta_error(status, code, message="error"):
    return _response(status, {"error": {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "x"}})


def _checkout(client, variant, address, shipping_setup, *, method="cod"):
    client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    response = client.post(
        CHECKOUT_URL,
        {"address_id": address.id, "shipping_method_id": shipping_setup["method"].id, "payment_method": method},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201, response.content
    return Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])


def _guest_checkout(variant, governorate, shipping_setup, *, phone="01001234567"):
    client = APIClient()
    client.post(CART_ITEMS_URL, {"variant_id": variant.id, "quantity": 1}, format="json")
    response = client.post(
        CHECKOUT_URL,
        {
            "contact": {"full_name": "Guest Buyer", "phone": phone, "email": ""},
            "address": {"governorate": governorate.id, "district": "Downtown", "street": "12 Tahrir St", "building": "3"},
            "shipping_method_id": shipping_setup["method"].id,
            "payment_method": "cod",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 201, response.content
    return Order.objects.get(order_number=response.json()["data"]["order"]["order_number"])


def _sent(delay):
    return [c.kwargs for c in delay.call_args_list]


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    "01001234567",
    "1001234567",
    "+201001234567",
    "+20 100 123 4567",
    "+20-100-123-4567",
    "00201001234567",
    "201001234567",
    "٠١٠٠١٢٣٤٥٦٧",
    " 0100 123 4567 ",
])
def test_egyptian_mobiles_become_e164(raw):
    from apps.notifications.whatsapp import to_e164

    assert to_e164(raw) == "+201001234567"


def test_gulf_numbers_keep_their_country_code():
    from apps.notifications.whatsapp import to_e164

    assert to_e164("+966 50 123 4567") == "+966501234567"
    assert to_e164("00971501234567") == "+971501234567"


@pytest.mark.parametrize("raw", ["", None, "abc", "0100", "0223456789", "+2001234"])
def test_what_whatsapp_cannot_reach_is_refused(raw):
    """Empty, junk, too short, and a Cairo landline (02...)."""
    from apps.notifications.whatsapp import to_e164

    assert to_e164(raw) == ""


# ---------------------------------------------------------------------------
# The Cloud API provider
# ---------------------------------------------------------------------------


def test_the_factory_returns_the_meta_provider(meta):
    from apps.notifications.providers import MetaCloudWhatsAppProvider, get_whatsapp_provider

    assert isinstance(get_whatsapp_provider(), MetaCloudWhatsAppProvider)


def test_the_meta_provider_refuses_to_start_without_credentials(meta):
    from apps.notifications.providers import get_whatsapp_provider

    meta.WHATSAPP_ACCESS_TOKEN = ""
    with pytest.raises(NotImplementedError):
        get_whatsapp_provider()


def test_a_template_is_sent_the_way_meta_documents_it(meta):
    from apps.notifications.providers import get_whatsapp_provider

    with mock.patch(POST, return_value=_response(200, ACCEPTED)) as post:
        result = get_whatsapp_provider().send_template(
            to="+201001234567", template_name="elreef_order_confirmed",
            params={"body": ["Jane Doe", "ELR-1001", "450.00 ج.م"]},
        )

    assert result["success"] is True
    assert result["provider_message_id"] == "wamid.HBgMMjAxMDAxMjM0NTY3FQIAERgSQ"
    url = post.call_args.args[0]
    assert url == "https://graph.facebook.com/v24.0/123456789012345/messages"
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer test-token"}
    assert post.call_args.kwargs["timeout"]
    assert post.call_args.kwargs["json"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "201001234567",
        "type": "template",
        "template": {
            "name": "elreef_order_confirmed",
            "language": {"code": "ar"},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Jane Doe"},
                    {"type": "text", "text": "ELR-1001"},
                    {"type": "text", "text": "450.00 ج.م"},
                ],
            }],
        },
    }


def test_a_template_without_parameters_sends_no_components(meta):
    from apps.notifications.providers import get_whatsapp_provider

    with mock.patch(POST, return_value=_response(200, ACCEPTED)) as post:
        get_whatsapp_provider().send_template(to="+201001234567", template_name="hello", params={})

    assert "components" not in post.call_args.kwargs["json"]["template"]


def test_parameters_meta_would_refuse_are_cleaned():
    """No newlines or tabs, no runs of spaces, never empty."""
    from apps.notifications.providers.meta_cloud import clean_param

    assert clean_param("12 Tahrir St\nFloor 3\t  Apt   5") == "12 Tahrir St Floor 3 Apt 5"
    assert clean_param("") == "-"
    assert clean_param(None) == "-"
    assert len(clean_param("x" * 5000)) == 1024


@pytest.mark.parametrize("response,retryable", [
    (_meta_error(400, 132001, "Template name does not exist in the translation"), False),
    (_meta_error(400, 131026, "Message undeliverable"), False),
    (_meta_error(401, 190, "Error validating access token"), False),
    (_meta_error(400, 131056, "pair rate limit hit"), True),
    (_meta_error(400, 130429, "Rate limit hit"), True),
    (_meta_error(429, 4, "Application request limit reached"), True),
    (_meta_error(500, 131000, "Something went wrong"), True),
    (_response(502, ValueError), True),
])
def test_failures_say_whether_a_retry_could_help(meta, response, retryable):
    from apps.notifications.providers import get_whatsapp_provider

    if response.json.return_value is ValueError:
        response.json.side_effect = ValueError
    with mock.patch(POST, return_value=response):
        result = get_whatsapp_provider().send_template(to="+201001234567", template_name="t", params={})

    assert result["success"] is False
    assert result["retryable"] is retryable
    assert f"HTTP {response.status_code}" in result["error"]
    assert "test-token" not in result["error"]


def test_a_connection_error_is_retried_but_an_unanswered_request_is_not(meta):
    """A read timeout may have been delivered already; a second try could send it twice."""
    from apps.notifications.providers import get_whatsapp_provider

    provider = get_whatsapp_provider()
    with mock.patch(POST, side_effect=requests.ConnectionError("refused")):
        assert provider.send_template(to="+201001234567", template_name="t", params={})["retryable"] is True
    with mock.patch(POST, side_effect=requests.ReadTimeout("slow")):
        assert provider.send_template(to="+201001234567", template_name="t", params={})["retryable"] is False


# ---------------------------------------------------------------------------
# The task: normalised number, delivery log, retries
# ---------------------------------------------------------------------------


def test_a_sent_message_is_logged_with_meta_message_id(meta, user):
    from apps.notifications.tasks import send_whatsapp_task

    with mock.patch(POST, return_value=_response(200, ACCEPTED)) as post:
        send_whatsapp_task(to="01001234567", template_name="t", params={"body": ["x"]}, event="order_created", user_id=user.id)

    assert post.call_args.kwargs["json"]["to"] == "201001234567"
    log = NotificationLog.objects.get(channel=NotificationChannel.WHATSAPP)
    assert log.success is True
    assert log.recipient == "+201001234567"
    assert log.provider_message_id == "wamid.HBgMMjAxMDAxMjM0NTY3FQIAERgSQ"
    assert log.user_id == user.id and log.event == "order_created"


def test_a_permanent_failure_is_logged_once_and_not_retried(meta):
    from apps.notifications.tasks import send_whatsapp_task

    with mock.patch(POST, return_value=_meta_error(400, 132001, "Template name does not exist")):
        result = send_whatsapp_task(to="+201001234567", template_name="nope", params={}, event="order_created")

    assert result["success"] is False
    log = NotificationLog.objects.get(channel=NotificationChannel.WHATSAPP)
    assert log.success is False and "132001" in log.error_message


def test_a_temporary_failure_is_retried(meta):
    from apps.notifications.tasks import send_whatsapp_task

    with mock.patch(POST, return_value=_meta_error(503, 131016, "Service unavailable")), pytest.raises(Exception):
        send_whatsapp_task(to="+201001234567", template_name="t", params={}, event="order_created")
    assert NotificationLog.objects.filter(channel=NotificationChannel.WHATSAPP, success=False).exists()


def test_an_invalid_number_is_logged_and_never_sent(meta):
    from apps.notifications.tasks import send_whatsapp_task

    with mock.patch(POST) as post:
        result = send_whatsapp_task(to="0223456789", template_name="t", params={}, event="order_created")

    assert post.call_count == 0
    assert result["success"] is False
    log = NotificationLog.objects.get(channel=NotificationChannel.WHATSAPP)
    assert log.success is False and log.recipient == "0223456789"


def test_missing_credentials_are_logged_not_raised(meta):
    from apps.notifications.tasks import send_whatsapp_task

    meta.WHATSAPP_PHONE_NUMBER_ID = ""
    with mock.patch(POST) as post:
        result = send_whatsapp_task(to="+201001234567", template_name="t", params={}, event="order_created")

    assert post.call_count == 0 and result["success"] is False
    assert "WHATSAPP_PHONE_NUMBER_ID" in NotificationLog.objects.get().error_message


# ---------------------------------------------------------------------------
# Orders send them
# ---------------------------------------------------------------------------


def test_nothing_is_sent_until_a_template_is_configured(
    authenticated_client, product_with_variant_and_stock, address, shipping_setup, django_capture_on_commit_callbacks,
):
    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)

    assert delay.call_count == 0


def test_a_signed_in_cod_order_sends_the_confirmation(
    whatsapp_on, authenticated_client, user, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        order = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)

    assert _sent(delay) == [{
        "to": "+201001234567",
        "template_name": "elreef_order_confirmed",
        "params": {"body": ["Jane Doe", order.order_number, f"{order.grand_total.amount:,.2f} ج.م"]},
        "event": "order_created",
        "user_id": user.id,
    }]


def test_a_guest_cod_order_sends_the_confirmation_to_the_phone_they_typed(
    whatsapp_on, product_with_variant_and_stock, governorate, shipping_setup, django_capture_on_commit_callbacks,
):
    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        order = _guest_checkout(product_with_variant_and_stock["variant"], governorate, shipping_setup, phone="01001234567")

    [sent] = _sent(delay)
    assert sent["to"] == "+201001234567"
    assert sent["template_name"] == "elreef_order_confirmed"
    assert sent["params"]["body"][:2] == ["Guest Buyer", order.order_number]
    assert sent["user_id"] is None


def test_the_whole_way_to_meta_and_into_the_log(
    whatsapp_on, meta, product_with_variant_and_stock, governorate, shipping_setup, django_capture_on_commit_callbacks,
):
    """Checkout -> on_commit -> Celery (eager) -> Cloud API (mocked) -> NotificationLog."""
    with mock.patch(POST, return_value=_response(200, ACCEPTED)) as post, \
            django_capture_on_commit_callbacks(execute=True):
        order = _guest_checkout(product_with_variant_and_stock["variant"], governorate, shipping_setup)

    payload = post.call_args.kwargs["json"]
    assert payload["to"] == "201001234567"
    assert payload["template"]["name"] == "elreef_order_confirmed"
    assert payload["template"]["components"][0]["parameters"][1] == {"type": "text", "text": order.order_number}
    log = NotificationLog.objects.get(channel=NotificationChannel.WHATSAPP)
    assert log.success is True and log.provider_message_id.startswith("wamid.")


def test_a_transfer_order_is_told_where_to_pay_then_confirmed(
    whatsapp_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    from apps.orders.services import transition_status

    whatsapp_on.PAYMENT_PROVIDERS = payment_providers_with(INSTAPAY=INSTAPAY_TEST_CONFIG)
    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        order = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup, method="instapay")

    [sent] = _sent(delay)
    assert sent["template_name"] == "elreef_awaiting_payment"
    assert sent["params"]["body"] == [
        "Jane Doe", order.order_number, f"{order.grand_total.amount:,.2f} ج.م", "alreef@instapay",
    ]
    assert order.status == OrderStatus.PENDING

    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        transition_status(order, OrderStatus.CONFIRMED)
    assert [s["template_name"] for s in _sent(delay)] == ["elreef_order_confirmed"]


def test_shipping_and_delivery_updates_follow_the_order(
    whatsapp_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    from apps.orders.services import transition_status

    order = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)
    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        for status in (OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED,
                       OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED):
            order = transition_status(order, status)

    sent = _sent(delay)
    assert [s["template_name"] for s in sent] == [
        "elreef_order_shipped", "elreef_out_for_delivery", "elreef_order_delivered",
    ]
    assert [s["event"] for s in sent] == ["order_shipped", "out_for_delivery", "order_delivered"]


def test_a_cancelled_order_is_told(
    whatsapp_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    from apps.orders.services import cancel_order

    order = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)
    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        cancel_order(order, reason="duplicate")

    assert [s["template_name"] for s in _sent(delay)] == ["elreef_order_cancelled"]
    assert "duplicate" not in str(_sent(delay))


def test_a_rolled_back_order_sends_nothing(
    whatsapp_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    from apps.orders.services import transition_status

    whatsapp_on.PAYMENT_PROVIDERS = payment_providers_with(INSTAPAY=INSTAPAY_TEST_CONFIG)
    order = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup, method="instapay")

    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                transition_status(order, OrderStatus.CONFIRMED)
                raise RuntimeError("later step failed")

    assert delay.call_count == 0


def test_a_broker_outage_costs_the_message_not_the_order(
    whatsapp_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    from kombu.exceptions import OperationalError

    with mock.patch(DELAY, side_effect=OperationalError("redis is down")), \
            mock.patch("apps.notifications.whatsapp.logger") as log, \
            django_capture_on_commit_callbacks(execute=True):
        order = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)

    assert Order.objects.filter(pk=order.pk).exists()
    assert log.exception.called


def test_parameters_come_from_settings(
    whatsapp_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    templates = _templates(order_confirmed="elreef_short_confirm")
    templates["order_confirmed"]["params"] = ["order_number", "first_name", "order_url"]
    whatsapp_on.WHATSAPP_ORDER_TEMPLATES = templates

    with mock.patch(DELAY) as delay, django_capture_on_commit_callbacks(execute=True):
        order = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)

    [sent] = _sent(delay)
    assert sent["template_name"] == "elreef_short_confirm"
    number, first_name, url = sent["params"]["body"]
    assert (number, first_name) == (order.order_number, "Jane")
    assert url.endswith(f"/account/orders/{order.order_number}")


def test_a_misnamed_parameter_is_logged_and_the_order_still_goes_through(
    whatsapp_on, authenticated_client, product_with_variant_and_stock, address, shipping_setup,
    django_capture_on_commit_callbacks,
):
    templates = _templates(order_confirmed="elreef_order_confirmed")
    templates["order_confirmed"]["params"] = ["customer_name", "tracking_number"]
    whatsapp_on.WHATSAPP_ORDER_TEMPLATES = templates

    with mock.patch(DELAY) as delay, mock.patch("apps.notifications.whatsapp.logger") as log, \
            django_capture_on_commit_callbacks(execute=True):
        order = _checkout(authenticated_client, product_with_variant_and_stock["variant"], address, shipping_setup)

    assert order.status == OrderStatus.CONFIRMED
    assert delay.call_count == 0
    assert "tracking_number" in str(log.error.call_args)


def test_the_settings_read_template_names_and_parameters_from_the_environment(monkeypatch):
    import importlib

    import config.settings.base as base

    monkeypatch.setenv("WHATSAPP_TEMPLATE_ORDER_SHIPPED", "elreef_shipped_v2")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_ORDER_SHIPPED_PARAMS", "first_name,order_number,order_url")
    try:
        reloaded = importlib.reload(base)
        assert reloaded.WHATSAPP_ORDER_TEMPLATES["order_shipped"] == {
            "name": "elreef_shipped_v2", "params": ["first_name", "order_number", "order_url"],
        }
        assert reloaded.WHATSAPP_ORDER_TEMPLATES["order_delivered"]["name"] == ""
    finally:
        monkeypatch.undo()
        importlib.reload(base)
