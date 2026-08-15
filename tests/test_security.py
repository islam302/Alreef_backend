"""Regression tests for the security-hardening pass: file-upload validator
wiring, fail-closed webhook defaults, and throttle-scope coverage on
sensitive auth endpoints."""
import pytest

from apps.brands.models import Brand
from apps.catalog.models import ProductVideo
from apps.categories.models import Category
from apps.core.validators import validate_image_file
from apps.shipping.providers.base import ShippingProvider
from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "model,field_name",
    [
        (Category, "image"),
        (Brand, "logo"),
        (User, "profile_image"),
        (ProductVideo, "thumbnail"),
    ],
)
def test_image_upload_fields_use_shared_validator(model, field_name):
    """Every ImageField that accepts user/staff-uploaded content must run
    through validate_image_file (extension + size + magic-byte + dimension
    checks) — not just Product.image and Review.image."""
    field = model._meta.get_field(field_name)
    assert validate_image_file in field.validators


class _MinimalShippingProvider(ShippingProvider):
    code = "test-carrier"

    def create_shipment(self, *, shipment, order):
        raise NotImplementedError

    def cancel_shipment(self, *, shipment):
        raise NotImplementedError

    def get_tracking(self, *, shipment):
        raise NotImplementedError


def test_shipping_provider_verify_webhook_defaults_fail_closed():
    """A concrete carrier adapter that forgets to override verify_webhook()
    must reject webhooks by default, not silently accept unsigned callbacks."""
    provider = _MinimalShippingProvider()
    assert provider.verify_webhook(headers={}, body=b"{}") is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/logout/",
        "/api/v1/auth/token/refresh/",
        "/api/v1/auth/change-password/",
        "/api/v1/auth/password-reset-confirm/",
        "/api/v1/auth/verify-email/",
        "/api/v1/auth/verify-email/confirm/",
        "/api/v1/auth/verify-phone/confirm/",
    ],
)
def test_sensitive_auth_endpoints_are_throttle_scoped(path):
    """These endpoints either let a caller guess a secret (refresh/verify/
    reset token, OTP) or are otherwise abuse-prone. Confirms each carries an
    explicit throttle_scope — resolving the view and checking the class
    attribute directly rather than burning 429s guessing a rate."""
    from django.urls import resolve

    view_class = resolve(path).func.view_class
    assert getattr(view_class, "throttle_scope", None) in {"auth", "auth_confirm"}
