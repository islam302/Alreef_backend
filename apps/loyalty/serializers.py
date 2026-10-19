from rest_framework import serializers

from apps.loyalty.models import LoyaltyAccount, LoyaltyTransaction


class LoyaltyAccountSerializer(serializers.ModelSerializer):
    tier_name = serializers.SerializerMethodField()

    class Meta:
        model = LoyaltyAccount
        fields = ["points_balance", "lifetime_points_earned", "tier_name"]

    def get_tier_name(self, obj) -> str | None:
        tier = obj.current_tier
        return tier.name if tier else None


class LoyaltyTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyTransaction
        fields = ["transaction_type", "points", "order", "note", "created_at", "expires_at"]


class LoyaltyRedeemSerializer(serializers.Serializer):
    points = serializers.IntegerField(min_value=1)
