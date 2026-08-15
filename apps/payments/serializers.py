from rest_framework import serializers

from apps.payments.models import Payment, PaymentTransaction, Refund


class PaymentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)

    class Meta:
        model = Payment
        fields = ["id", "order_number", "provider", "method", "amount", "status", "provider_reference", "metadata", "created_at"]
        read_only_fields = fields


class PaymentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTransaction
        fields = ["transaction_type", "amount", "success", "provider_transaction_id", "created_at"]


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = ["id", "payment", "order", "amount", "reason", "destination", "status", "provider_refund_id", "processed_at", "created_at"]
        read_only_fields = fields


class RefundCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    destination = serializers.ChoiceField(choices=["original_method", "store_credit"], default="original_method")
