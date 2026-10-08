from django.conf import settings
from rest_framework import serializers

from apps.carts.models import Cart, CartItem
from apps.catalog.serializers import ProductVariantSerializer


class CartItemSerializer(serializers.ModelSerializer):
    variant = ProductVariantSerializer(read_only=True)
    product_name = serializers.CharField(source="variant.product.name_en", read_only=True)
    # The storefront is Arabic-first; `product_name` stays as it was for any
    # existing consumer, and this is the name the shop actually displays.
    product_name_ar = serializers.CharField(source="variant.product.name_ar", read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ["id", "variant", "product_name", "product_name_ar", "quantity", "unit_price_snapshot", "line_total"]

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


def _validate_quantity(value):
    """
    One shared ceiling for both cart serializers.

    Read from settings at call time rather than baked into `max_value` at import,
    so a deployment can change it and a test can override it.

    Both halves of this mattered. Without a ceiling the cart accepted a
    two-billion-unit line and carried it into every totals calculation
    downstream; and a quantity at or above 2**31 got past validation entirely and
    was rejected by the integer column instead, which reached the customer as a
    500 with `INTERNAL_ERROR` rather than a field error they could act on.
    """
    ceiling = settings.MAX_CART_ITEM_QUANTITY
    if value > ceiling:
        raise serializers.ValidationError(
            f"At most {ceiling} of the same item per order. "
            f"Please contact us for a larger quantity."
        )
    return value


class AddCartItemSerializer(serializers.Serializer):
    variant_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1, validators=[_validate_quantity])


class UpdateCartItemSerializer(serializers.Serializer):
    # 0 is allowed here and means "remove this line".
    quantity = serializers.IntegerField(min_value=0, validators=[_validate_quantity])


class ApplyCouponSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
