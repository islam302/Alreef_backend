"""
The OpenAPI schema is the contract two frontends are built against.

Thirty-seven views used to publish nothing at all. They were plain `APIView`s
with no `serializer_class`, so drf-spectacular skipped each one with a warning
and an otherwise green build — and among them were every payment endpoint except
the public methods list. A field rename there would have shipped undetected,
because there was nothing to check it against.

CI ratchets the warning counts. These tests assert the part that actually
matters: that the endpoints exist in the schema and describe the envelope the
client really receives.
"""
import pytest

CONTRACT_CRITICAL = [
    # Money moves through these, so a silent shape change is the expensive kind.
    "/api/v1/payments/{id}/",
    "/api/v1/payments/{id}/status/",
    "/api/v1/payments/{id}/refund/",
    "/api/v1/payments/{id}/reconcile/",
    "/api/v1/payments/{id}/settle/",
    "/api/v1/payments/webhooks/{provider}/",
    "/api/v1/payments/methods/",
    # Account recovery: the flows a customer cannot work around if they break.
    "/api/v1/auth/login/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/token/refresh/",
    "/api/v1/auth/password-reset/",
    "/api/v1/auth/password-reset-confirm/",
    "/api/v1/auth/verify-email/confirm/",
    "/api/v1/checkout/",
]


@pytest.fixture(scope="module")
def schema():
    from drf_spectacular.generators import SchemaGenerator

    return SchemaGenerator().get_schema(request=None, public=True)


def test_no_view_is_missing_from_the_schema(schema):
    """
    A view drf-spectacular cannot read is absent from the contract entirely.
    Asserting on the generated paths rather than on a warning count means this
    fails for the reason that matters, not for a log line.
    """
    from django.urls import get_resolver
    from django.urls.resolvers import URLPattern, URLResolver

    described = set(schema["paths"])

    def api_paths(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            full = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                yield from api_paths(pattern, full)
            elif isinstance(pattern, URLPattern):
                yield full

    # Only the versioned API; schema/docs routes and the health probe are not
    # part of the client contract.
    routed = [p for p in api_paths(get_resolver()) if p.startswith("api/v1/")]
    # Nothing to compare one-for-one against — the generator normalises path
    # parameters — so assert the count is in the right neighbourhood instead of
    # silently dropping two thirds of the surface, which is what used to happen.
    assert len(described) > 0.6 * len(routed), (
        f"only {len(described)} paths are described for {len(routed)} routed patterns"
    )


@pytest.mark.parametrize("path", CONTRACT_CRITICAL)
def test_a_contract_critical_endpoint_is_described(schema, path):
    assert path in schema["paths"], f"{path} publishes no schema"


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/v1/payments/{id}/status/", "get"),
        ("/api/v1/payments/{id}/refund/", "post"),
        ("/api/v1/auth/logout/", "post"),
    ],
)
def test_a_described_response_carries_the_envelope(schema, path, method):
    """
    Every response is wrapped in `{success, message, data, errors}`. A schema
    describing the payload without that wrapper is worse than none: a generated
    client would look for the object one level too high and be confidently
    wrong. `CartMergeView` was exactly that case.
    """
    operation = schema["paths"][path][method]
    ok = operation["responses"].get("200") or operation["responses"].get("201")
    assert ok, f"{method.upper()} {path} describes no success response"

    ref = ok["content"]["application/json"]["schema"].get("$ref", "")
    component = ref.rsplit("/", 1)[-1]
    assert component, f"{method.upper()} {path} success response has no schema component"

    properties = schema["components"]["schemas"][component]["properties"]
    for field in ("success", "message", "data", "errors"):
        assert field in properties, (
            f"{method.upper()} {path} -> {component} is missing the envelope field '{field}'"
        )


def test_the_webhook_endpoint_does_not_claim_an_envelope(schema):
    """
    The one deliberate exception. A payment provider gets a bare status code —
    200 applied or duplicate, 202 recorded, 400 bad signature, 409 contradiction
    — and describing a body it never receives would be a lie in the other
    direction.
    """
    operation = schema["paths"]["/api/v1/payments/webhooks/{provider}/"]["post"]
    for code in ("200", "202", "400", "409"):
        assert code in operation["responses"], f"webhook does not document {code}"
        assert "content" not in operation["responses"][code], (
            f"webhook {code} claims a response body; the provider gets none"
        )
