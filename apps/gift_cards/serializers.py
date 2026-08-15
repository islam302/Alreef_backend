from rest_framework import serializers

from apps.gift_cards.models import GiftCard, StoreCreditAccount, StoreCreditTransaction


class GiftCardPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = GiftCard
        fields = ["masked_code", "balance", "status", "expires_at"]


class GiftCardCheckSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)


class StoreCreditAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreCreditAccount
        fields = ["balance"]


class StoreCreditTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreCreditTransaction
        fields = ["amount", "reason", "order", "created_at"]
