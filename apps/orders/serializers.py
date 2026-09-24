from rest_framework import serializers

from apps.orders.models import (
    Order,
    OrderItem,
    OrderStatusHistory,
    ReturnItem,
    ReturnRequest,
)


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id", "product", "variant", "product_name_snapshot", "variant_name_snapshot", "sku_snapshot",
            "unit_price", "quantity", "discount_amount", "tax_amount", "line_total",
        ]


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderStatusHistory
        fields = ["from_status", "to_status", "note", "created_at"]


class OrderListSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "order_number", "status", "grand_total", "currency", "item_count", "created_at"]

    def get_item_count(self, obj):
        return sum(i.quantity for i in obj.items.all())


class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "order_number", "status", "currency", "customer_name", "customer_email", "customer_phone",
            "shipping_address_snapshot", "shipping_method_name", "payment_method",
            "subtotal", "coupon_code", "coupon_discount", "promotion_discount", "loyalty_discount",
            "gift_card_amount_used", "store_credit_amount_used", "tax_total", "shipping_total", "cod_fee",
            "grand_total", "notes", "cancel_reason", "items", "status_history",
            "confirmed_at", "shipped_at", "delivered_at", "cancelled_at", "created_at",
        ]
        read_only_fields = fields


class OrderCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class ReturnItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReturnItem
        fields = ["order_item", "quantity"]


class ReturnRequestSerializer(serializers.ModelSerializer):
    items = ReturnItemSerializer(many=True, read_only=True)

    class Meta:
        model = ReturnRequest
        fields = ["id", "order", "reason", "description", "status", "resolution_note", "items", "created_at"]
        read_only_fields = ["id", "status", "resolution_note", "created_at"]


class ReturnRequestCreateSerializer(serializers.Serializer):
    reason = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True, default="")
    items = serializers.ListField(child=serializers.DictField(), allow_empty=False, help_text='[{"order_item_id": 1, "quantity": 1}]')
