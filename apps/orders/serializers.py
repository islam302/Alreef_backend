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


class GuestOrderLookupSerializer(serializers.Serializer):
    """Either the token checkout returned, or the order number and its phone."""

    token = serializers.CharField(required=False, allow_blank=True, max_length=512, default="")
    order_number = serializers.CharField(required=False, allow_blank=True, max_length=30, default="")
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32, default="")

    def validate(self, attrs):
        if attrs["token"]:
            return attrs
        if attrs["order_number"].strip() and attrs["phone"].strip():
            return attrs
        raise serializers.ValidationError("Send the order number and the phone it was placed with.")


class GuestOrderStatusSerializer(serializers.ModelSerializer):
    """
    What a guest sees when they look their order up.

    Narrower than the account view on purpose. The phone-based lookup is the
    weaker of the two keys, so nothing here should be worth more to a stranger
    than the order number and phone they already hold: no street, building or
    landmark, no email, no staff notes from the status history. Where it is
    going is shown as governorate and city — enough for the customer to know
    it is theirs.
    """

    items = serializers.SerializerMethodField()
    status_history = serializers.SerializerMethodField()
    destination = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_number", "status", "currency", "payment_method", "shipping_method_name", "destination",
            "subtotal", "coupon_discount", "promotion_discount", "tax_total", "shipping_total", "cod_fee",
            "grand_total", "items", "status_history", "payment",
            "confirmed_at", "shipped_at", "delivered_at", "cancelled_at", "created_at",
        ]
        read_only_fields = fields

    def get_items(self, obj):
        return [
            {
                "product_name_snapshot": item.product_name_snapshot,
                "variant_name_snapshot": item.variant_name_snapshot,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price.amount),
                "line_total": str(item.line_total.amount),
            }
            for item in obj.items.all()
        ]

    def get_status_history(self, obj):
        return [{"to_status": h.to_status, "created_at": h.created_at} for h in obj.status_history.all()]

    def get_destination(self, obj):
        snapshot = obj.shipping_address_snapshot or {}
        return {"governorate": snapshot.get("governorate", ""), "city": snapshot.get("city", "")}

    def get_payment(self, obj):
        payment = obj.payments.order_by("-created_at").first()
        if payment is None:
            return None
        metadata = payment.metadata or {}
        return {
            "id": payment.id,
            "method": payment.method,
            "status": payment.status,
            # The same published instructions checkout returned: where to send a
            # transfer and what to quote. Not secrets.
            "instructions_ar": metadata.get("instructions_ar", ""),
            "pay_to": metadata.get("instapay_address", ""),
            "pay_reference": metadata.get("transfer_reference", obj.order_number),
        }


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
