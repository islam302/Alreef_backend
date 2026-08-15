"""
Every non-3xx JSON API response must use the standard envelope defined in
apps/core/response.py: {success: bool, message: str, data, errors}.
"""
import pytest

from apps.core.error_codes import ErrorCodes

pytestmark = pytest.mark.django_db

ENVELOPE_KEYS = {"success", "message", "data", "errors"}


def test_health_check_endpoint_returns_ok(api_client):
    response = api_client.get("/api/health/")
    assert response.status_code == 200


def test_successful_response_envelope_shape(api_client, category):
    response = api_client.get("/api/v1/categories/")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == ENVELOPE_KEYS
    assert body["success"] is True
    assert isinstance(body["message"], str)
    assert body["data"] is not None
    assert body["errors"] is None


def test_404_response_envelope_shape(api_client):
    response = api_client.get("/api/v1/products/this-slug-does-not-exist/")

    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) == ENVELOPE_KEYS
    assert body["success"] is False
    assert isinstance(body["message"], str)
    assert body["data"] is None
    assert body["errors"]["code"] == ErrorCodes.NOT_FOUND


def test_validation_error_response_envelope_shape(api_client):
    # Missing required "email" and "password" fields.
    response = api_client.post("/api/v1/auth/register/", {"first_name": "No Email"}, format="json")

    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) == ENVELOPE_KEYS
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"]["code"] == ErrorCodes.VALIDATION_ERROR
    assert "email" in body["errors"]["fields"]


def test_paginated_response_envelope_shape(api_client, product_with_variant_and_stock):
    response = api_client.get("/api/v1/products/")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == ENVELOPE_KEYS
    assert body["success"] is True
    assert {"results", "count", "num_pages", "current_page", "page_size", "next", "previous"} <= set(body["data"].keys())


def test_business_error_uses_application_error_code(api_client):
    response = api_client.post("/api/v1/auth/login/", {"email": "nobody@example.com", "password": "whatever"}, format="json")

    assert response.status_code == 401
    body = response.json()
    assert set(body.keys()) == ENVELOPE_KEYS
    assert body["errors"]["code"] == ErrorCodes.AUTH_INVALID_CREDENTIALS
