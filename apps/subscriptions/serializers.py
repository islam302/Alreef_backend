from rest_framework import serializers

from apps.subscriptions.models import (
    Subscription,
    SubscriptionItem,
    SubscriptionSchedule,
)


class SubscriptionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionItem
        fields = ["id", "variant", "quantity"]


class SubscriptionScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionSchedule
        fields = ["scheduled_date", "status", "order", "failure_reason"]


class SubscriptionSerializer(serializers.ModelSerializer):
    items = SubscriptionItemSerializer(many=True, read_only=True)
    schedule_entries = SubscriptionScheduleSerializer(many=True, read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id", "name", "address", "shipping_method", "payment_method", "frequency", "custom_interval_days",
            "status", "next_order_date", "last_order_date", "items", "schedule_entries", "created_at",
        ]
        read_only_fields = ["id", "status", "last_order_date", "schedule_entries", "created_at"]


class SubscriptionItemInputSerializer(serializers.Serializer):
    # Typed and bounded. A free-form dict let a non-numeric id or a negative,
    # absurd or non-numeric quantity through to the database, which answered
    # with a 500 instead of a field error.
    variant_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, default=1)

    def validate_quantity(self, value):
        from django.conf import settings

        if value > settings.MAX_CART_ITEM_QUANTITY:
            raise serializers.ValidationError(f"At most {settings.MAX_CART_ITEM_QUANTITY} of the same item.")
        return value


class SubscriptionCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    address_id = serializers.IntegerField()
    shipping_method_id = serializers.IntegerField()
    payment_method = serializers.CharField()
    frequency = serializers.ChoiceField(choices=["weekly", "monthly", "quarterly", "custom"])
    custom_interval_days = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1, max_value=365,
    )
    start_date = serializers.DateField()
    items = SubscriptionItemInputSerializer(many=True, allow_empty=False)
