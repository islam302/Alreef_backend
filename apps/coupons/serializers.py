from rest_framework import serializers

from apps.coupons.models import Coupon


class CouponPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            "code", "description", "discount_type", "percentage_value", "fixed_amount_value",
            "minimum_order_amount", "maximum_discount_amount", "expiration_date",
        ]
