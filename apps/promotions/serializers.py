from rest_framework import serializers

from apps.promotions.models import Promotion


class PromotionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Promotion
        fields = [
            "id", "name", "description", "promotion_type", "percentage_value", "fixed_amount_value",
            "buy_quantity", "get_quantity", "get_discount_percent", "start_date", "end_date",
        ]
