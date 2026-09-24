from rest_framework import serializers

from apps.shipping.models import City, Governorate, ShipmentEvent, ShippingMethod


class GovernorateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Governorate
        fields = ["id", "code", "name_en", "name_ar"]


class CitySerializer(serializers.ModelSerializer):
    class Meta:
        model = City
        fields = ["id", "name_en", "name_ar", "governorate"]


class ShippingMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingMethod
        fields = ["id", "name", "method_type", "description", "min_delivery_days", "max_delivery_days"]


class ShippingQuoteSerializer(serializers.Serializer):
    method_id = serializers.IntegerField(source="method.id")
    method_name = serializers.CharField(source="method.name")
    method_type = serializers.CharField(source="method.method_type")
    min_delivery_days = serializers.IntegerField(source="method.min_delivery_days")
    max_delivery_days = serializers.IntegerField(source="method.max_delivery_days")
    price = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    is_free = serializers.BooleanField()

    def get_price(self, obj):
        return str(obj.price.amount)

    def get_currency(self, obj):
        return str(obj.price.currency)


class ShipmentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentEvent
        fields = ["status", "description", "location", "occurred_at"]


class ShipmentTrackingSerializer(serializers.Serializer):
    tracking_number = serializers.CharField()
    carrier_code = serializers.CharField()
    status = serializers.CharField()
    estimated_delivery = serializers.DateField(allow_null=True)
    delivered_at = serializers.DateTimeField(allow_null=True)
    events = ShipmentEventSerializer(many=True)
