from rest_framework import serializers

from apps.carts.models import Cart, CartItem
from apps.catalog.serializers import ProductVariantSerializer


class CartItemSerializer(serializers.ModelSerializer):
    variant = ProductVariantSerializer(read_only=True)
    product_name = serializers.CharField(source="variant.product.name_en", read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ["id", "variant", "product_name", "quantity", "unit_price_snapshot", "line_total"]

    def get_line_total(self, obj):
        return str(obj.line_total.amount)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ["id", "currency", "coupon_code", "items", "subtotal", "item_count", "created_at", "updated_at"]

    def get_subtotal(self, obj):
        from apps.carts.services import get_cart_subtotal

        return str(get_cart_subtotal(obj).amount)

    def get_item_count(self, obj):
        return sum(item.quantity for item in obj.items.all())


class AddCartItemSerializer(serializers.Serializer):
    variant_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0)


class ApplyCouponSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
