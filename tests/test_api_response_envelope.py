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


# ---------------------------------------------------------------------------
# The whole surface, not a sample
# ---------------------------------------------------------------------------


def _public_get_paths():
    """Every /api/v1/ GET route that takes no path arguments.

    Enumerated from the URLconf rather than listed by hand, so a route added
    next month is covered the day it is added.
    """
    from django.urls import URLPattern, URLResolver, get_resolver

    paths = []

    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern, prefix + str(pattern.pattern))
                continue
            if not isinstance(pattern, URLPattern):
                continue
            route = prefix + str(pattern.pattern)
            if not route.startswith("api/v1/"):
                continue
            # Skip anything parameterised, admin-only, or not a plain GET.
            if any(token in route for token in ("<", "(?P<", "admin-api", "format")):
                continue
            view = getattr(pattern.callback, "cls", None) or getattr(
                pattern.callback, "view_class", None
            )
            if view is None or not hasattr(view, "get"):
                continue
            # A router's index is a DRF navigation affordance listing route
            # names, not part of the data contract — nothing consumes it.
            if view.__name__.endswith("APIRootView"):
                continue
            paths.append("/" + route)

    walk(get_resolver())
    return sorted(set(paths))


def test_every_public_get_endpoint_answers_in_the_envelope(api_client, category, governorate):
    """The contract is "every endpoint, one envelope", and the frontend's
    client unwraps `data` unconditionally on that promise. An endpoint that
    answers with a bare array does not look broken in a browser — it breaks
    the screen that consumes it, one screen away.

    This is how /shipping/governorates/ and /shipping/cities/ were found:
    both set `pagination_class = None`, which is correct for a short
    reference list, but the envelope is applied BY the pagination class, so
    turning it off silently dropped it. The address form then read
    `undefined`, and checkout was unreachable.
    """
    offenders = []

    for path in _public_get_paths():
        response = api_client.get(path)
        if response.status_code >= 500:
            offenders.append(f"{path} -> HTTP {response.status_code}")
            continue
        if response.status_code in (301, 302, 404, 405):
            continue
        if "application/json" not in response.headers.get("Content-Type", ""):
            continue

        body = response.json()
        if not isinstance(body, dict) or set(body.keys()) != ENVELOPE_KEYS:
            shape = "array" if isinstance(body, list) else sorted(body)[:6]
            offenders.append(f"{path} -> {shape}")

    assert not offenders, "endpoints not using the standard envelope:\n  " + "\n  ".join(offenders)
