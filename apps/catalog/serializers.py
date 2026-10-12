from rest_framework import serializers

from apps.attributes.serializers import AttributeValueSerializer
from apps.brands.serializers import BrandSerializer
from apps.categories.serializers import CategoryListSerializer
from apps.catalog.models import (
    BundleItem,
    Product,
    ProductImage,
    ProductVariant,
    ProductVideo,
)
from apps.inventory.services import get_or_create_item


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "image", "alt_text", "is_primary", "sort_order", "variant"]


class ProductVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVideo
        fields = ["id", "video_url", "thumbnail", "provider", "duration_seconds"]


def _stock_fields(item) -> dict:
    return {
        "available_quantity": item.available_quantity,
        "is_low_stock": item.is_low_stock,
        "is_out_of_stock": item.is_out_of_stock,
    }


class ProductVariantSerializer(serializers.ModelSerializer):
    attributes = AttributeValueSerializer(source="attribute_values", many=True, read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "id", "sku", "barcode", "name_suffix", "price", "compare_at_price", "weight_grams",
            "is_default", "attributes", "images", "stock",
        ]

    def get_stock(self, obj):
        item = get_or_create_item(obj)
        return _stock_fields(item)


class BundleItemSerializer(serializers.ModelSerializer):
    component = ProductVariantSerializer(source="component_variant", read_only=True)

    class Meta:
        model = BundleItem
        fields = ["id", "component", "quantity"]


class ProductListSerializer(serializers.ModelSerializer):
    brand = BrandSerializer(read_only=True)
    category = CategoryListSerializer(read_only=True)
    primary_image = serializers.SerializerMethodField()
    computed_badges = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "name_en", "name_ar", "slug", "short_description", "brand", "category",
            "price", "compare_at_price", "product_type", "badges", "computed_badges",
            "primary_image", "in_stock", "featured",
        ]

    def get_primary_image(self, obj):
        image = next((i for i in obj.images.all() if i.is_primary), None) or next(iter(obj.images.all()), None)
        return ProductImageSerializer(image).data if image else None

    def get_computed_badges(self, obj):
        badges = list(obj.badges)
        variants = list(obj.variants.filter(is_active=True))
        if variants:
            items = [get_or_create_item(v) for v in variants]
            if all(i.is_out_of_stock for i in items):
                badges.append("OUT_OF_STOCK")
            elif any(i.is_low_stock for i in items):
                badges.append("LOW_STOCK")
        if obj.compare_at_price and obj.price < obj.compare_at_price:
            badges.append("SALE")
        return badges

    def get_in_stock(self, obj):
        variants = list(obj.variants.filter(is_active=True))
        # A product with no size cannot go in a cart (cart lines are sizes), so
        # it is not "in stock" — reporting True put a dead add button on cards.
        if not variants:
            return False
        return any(not get_or_create_item(v).is_out_of_stock for v in variants)


class ProductDetailSerializer(serializers.ModelSerializer):
    brand = BrandSerializer(read_only=True)
    category = CategoryListSerializer(read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    videos = ProductVideoSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    attributes = AttributeValueSerializer(source="attribute_values", many=True, read_only=True)
    bundle_items = BundleItemSerializer(many=True, read_only=True)
    computed_badges = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "name_en", "name_ar", "slug", "sku", "barcode", "description", "short_description",
            "brand", "category", "product_type", "price", "compare_at_price", "tax_category",
            "weight_grams", "packaging_type", "badges", "computed_badges", "food_metadata", "honey_metadata",
            "images", "videos", "variants", "attributes", "bundle_items", "seo_title", "seo_description",
        ]

    def get_computed_badges(self, obj):
        return ProductListSerializer().get_computed_badges(obj)
