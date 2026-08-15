from rest_framework import serializers

from apps.orders.models import PaymentMethodSnapshot
from apps.orders.serializers import OrderDetailSerializer


class CheckoutInputSerializer(serializers.Serializer):
    address_id = serializers.IntegerField()
    shipping_method_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=PaymentMethodSnapshot.choices)
    coupon_code = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class CheckoutTotalsSerializer(serializers.Serializer):
    subtotal = serializers.SerializerMethodField()
    coupon_discount = serializers.SerializerMethodField()
    promotion_discount = serializers.SerializerMethodField()
    tax_total = serializers.SerializerMethodField()
    shipping_total = serializers.SerializerMethodField()
    cod_fee = serializers.SerializerMethodField()
    grand_total = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    free_shipping = serializers.BooleanField()

    def get_subtotal(self, obj):
        return str(obj.subtotal.amount)

    def get_coupon_discount(self, obj):
        return str(obj.coupon_discount.amount)

    def get_promotion_discount(self, obj):
        return str(obj.promotion_discount.amount)

    def get_tax_total(self, obj):
        return str(obj.tax_total.amount)

    def get_shipping_total(self, obj):
        return str(obj.shipping_total.amount)

    def get_cod_fee(self, obj):
        return str(obj.cod_fee.amount)

    def get_grand_total(self, obj):
        return str(obj.grand_total.amount)

    def get_currency(self, obj):
        return str(obj.grand_total.currency)


class CheckoutResultSerializer(serializers.Serializer):
    order = OrderDetailSerializer()
    payment = serializers.DictField()
