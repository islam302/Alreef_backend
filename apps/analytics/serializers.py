import json

from django.conf import settings
from rest_framework import serializers

from apps.analytics.models import AnalyticsEventType


def _depth(value, level=1):
    """Deepest nesting level inside a JSON-ish structure."""
    if isinstance(value, dict):
        children = value.values()
    elif isinstance(value, (list, tuple)):
        children = value
    else:
        return level
    return max((_depth(child, level + 1) for child in children), default=level)


class TrackEventSerializer(serializers.Serializer):
    """
    The public, unauthenticated event tracker's input.

    `event_type` was always a ChoiceField, so the taxonomy itself could never be
    polluted. `metadata` had no bounds at all, and that was the hole: an
    anonymous caller could store a megabyte of arbitrary JSON per request —
    1,011,890 characters, measured — at 60 requests a minute per address, which
    is tens of megabytes a minute of attacker-chosen rows plus the same volume
    through the Celery broker. Nobody watches the analytics table, so it would
    have filled a disk quietly.
    """

    event_type = serializers.ChoiceField(choices=AnalyticsEventType.choices)
    product_id = serializers.IntegerField(required=False, allow_null=True)
    # Accepted, but never trusted: the view drops it unless the caller owns that
    # order. See TrackEventView.
    order_id = serializers.IntegerField(required=False, allow_null=True)
    metadata = serializers.DictField(required=False, default=dict)

    def validate_metadata(self, value):
        if not value:
            return value

        if len(value) > settings.ANALYTICS_METADATA_MAX_KEYS:
            raise serializers.ValidationError(
                f"At most {settings.ANALYTICS_METADATA_MAX_KEYS} keys are allowed."
            )

        if _depth(value) > settings.ANALYTICS_METADATA_MAX_DEPTH:
            raise serializers.ValidationError(
                f"Nested more than {settings.ANALYTICS_METADATA_MAX_DEPTH} levels deep."
            )

        # Measure what will actually be stored, not len() of the dict. Rejected
        # rather than truncated: silently keeping half of a client's telemetry is
        # worse than telling it the payload was refused.
        try:
            encoded = json.dumps(value, default=str)
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError("Metadata must be JSON-serialisable.") from exc

        if len(encoded) > settings.ANALYTICS_METADATA_MAX_CHARS:
            raise serializers.ValidationError(
                f"Metadata is too large ({len(encoded)} characters); "
                f"the maximum is {settings.ANALYTICS_METADATA_MAX_CHARS}."
            )
        return value
