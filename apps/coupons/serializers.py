from rest_framework import serializers

from apps.coupons.models import Coupon


class CouponPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            "code", "description", "discount_type", "percentage_value", "fixed_amount_value",
            "minimum_order_amount", "maximum_discount_amount", "expiration_date",
        ]


class WelcomeOfferRequestSerializer(serializers.Serializer):
    # Optional on issue: a device that already holds a token sends it back to
    # have its terms read by the server, instead of trusting what it stored.
    token = serializers.CharField(required=False, allow_blank=True, max_length=512)


class WelcomeOfferClaimSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512)


class WelcomeOfferSerializer(serializers.Serializer):
    token = serializers.CharField()
    percentage = serializers.IntegerField()
    expires_at = serializers.DateTimeField()


class WelcomeCouponSerializer(serializers.Serializer):
    code = serializers.CharField()
    percentage = serializers.IntegerField(source="percentage_value")
    expires_at = serializers.DateTimeField(source="expiration_date")
