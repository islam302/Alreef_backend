from rest_framework import serializers

from apps.payments.models import Payment, PaymentTransaction, Refund


class PaymentMethodSerializer(serializers.Serializer):
    """
    One offerable payment method, for the storefront's checkout picker.

    Serialises a `config.MethodSpec`, not a model. Carries the labels and
    instructions so neither frontend keeps its own copy of the wording, and
    carries `redirects` so the UI knows whether to expect to send the customer
    away. Deliberately omits every reason a method might have been unavailable:
    those name our configuration.
    """

    code = serializers.CharField(source="method")
    label_ar = serializers.CharField()
    label_en = serializers.CharField()
    instructions_ar = serializers.CharField()
    #: True when paying means leaving our site.
    redirects = serializers.BooleanField()
    sort_order = serializers.IntegerField()
    cod_fee = serializers.SerializerMethodField()

    def get_cod_fee(self, spec):
        """The surcharge, as a string, or null. Only COD has one today."""
        if spec.method != "cod":
            return None
        from apps.payments.config import cod_limits

        fee, _minimum, _maximum = cod_limits()
        return str(fee)


class ManualSettlementSerializer(serializers.Serializer):
    """
    Staff confirming that a manually-settled payment did or did not arrive.

    Covers cash on delivery and InstaPay, which share the shape: no gateway to
    ask, so a person says. `collected` is accepted alongside `received` because
    "collected" is the natural word for cash and the endpoint was published with
    it.
    """

    outcome = serializers.ChoiceField(choices=["received", "collected", "refused"])
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_outcome(self, value):
        return "received" if value == "collected" else value


class PaymentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "order_number", "provider", "method", "amount", "status",
            "provider_reference", "metadata", "failure_reason", "created_at", "updated_at",
        ]
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
