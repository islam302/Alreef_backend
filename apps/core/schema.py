"""
Describing the response envelope to drf-spectacular.

Every view in this project answers with the same wrapper —
`{success, message, data, errors}` from `apps.core.response.success_response` —
but the generator cannot see that. It infers a response shape from a
`serializer_class`, and a plain `APIView` returning a hand-built `Response` has
none, so thirty-seven views were skipped entirely with a `W002` warning each.

That mattered more than a warning count suggests: the schema is the only
machine-checkable contract between this API and two frontends, and the views it
was silent about included the whole payment surface. A field rename there ships
undetected.

`envelope()` gives those views a truthful response schema in one line, so the
contract describes what the client actually receives rather than the payload
stripped of its wrapper.
"""
from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

_COUNTER = {"n": 0}


def _unique_name(hint: str) -> str:
    """drf-spectacular needs a distinct component name per inline serializer."""
    _COUNTER["n"] += 1
    return f"{hint}Envelope{_COUNTER['n']}"


def envelope(data_field=None, *, name: str = "Response", many: bool = False):
    """
    The success envelope wrapped around `data_field`.

    `data_field` is a serializer class, a serializer instance, or None for the
    responses that carry no payload (a logout, a fire-and-forget event). Pass
    `many=True` for a list.

        @extend_schema(responses=envelope(PaymentSerializer, name="Payment"))
        class PaymentStatusView(APIView):
            ...
    """
    if data_field is None:
        data = serializers.JSONField(allow_null=True, required=False)
    else:
        instance = data_field() if isinstance(data_field, type) else data_field
        if many and not getattr(instance, "many", False):
            instance = type(instance)(many=True)
        data = instance

    return inline_serializer(
        name=_unique_name(name),
        fields={
            "success": serializers.BooleanField(default=True),
            "message": serializers.CharField(),
            "data": data,
            # Always present and always null on success. Clients branch on
            # `success`, so describing `errors` as nullable here is what stops a
            # generated client typing it as required.
            "errors": serializers.JSONField(allow_null=True, required=False),
        },
    )
