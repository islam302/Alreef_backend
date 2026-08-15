from rest_framework import serializers

from apps.analytics.models import AnalyticsEventType


class TrackEventSerializer(serializers.Serializer):
    event_type = serializers.ChoiceField(choices=AnalyticsEventType.choices)
    product_id = serializers.IntegerField(required=False, allow_null=True)
    order_id = serializers.IntegerField(required=False, allow_null=True)
    metadata = serializers.DictField(required=False, default=dict)
