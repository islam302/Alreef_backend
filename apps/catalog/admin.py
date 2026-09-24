from django.contrib import admin

from apps.catalog.models import (
    BundleItem,
    MixMatchBox,
    Product,
    ProductAttributeValue,
    ProductImage,
    ProductVariant,
    ProductVideo,
    VariantAttributeValue,
)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductVideoInline(admin.TabularInline):
    model = ProductVideo
    extra = 0


class ProductAttributeValueInline(admin.TabularInline):
    model = ProductAttributeValue
    extra = 1
    autocomplete_fields = ["attribute_value"]


class VariantAttributeValueInline(admin.TabularInline):
    model = VariantAttributeValue
    extra = 1
    autocomplete_fields = ["attribute_value"]


class ProductVariantInline(admin.StackedInline):
    model = ProductVariant
    extra = 0
    fields = ["sku", "barcode", "name_suffix", "price", "compare_at_price", "cost_price", "weight_grams", "is_active", "is_default", "sort_order"]
    show_change_link = True


class BundleItemInline(admin.TabularInline):
    model = BundleItem
    fk_name = "bundle"
    extra = 1
    autocomplete_fields = ["component_variant"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name_en", "sku", "category", "brand", "product_type", "status", "published", "featured", "price"]
    list_filter = ["status", "published", "featured", "product_type", "category", "brand"]
    search_fields = ["name_en", "name_ar", "sku", "barcode"]
    prepopulated_fields = {"slug": ("name_en",)}
    autocomplete_fields = ["brand", "category"]
    inlines = [ProductImageInline, ProductVideoInline, ProductAttributeValueInline, ProductVariantInline, BundleItemInline]
    fieldsets = (
        (None, {"fields": ("name_en", "name_ar", "slug", "sku", "barcode", "category", "brand", "product_type", "status", "published", "featured")}),
        ("Description", {"fields": ("short_description", "description")}),
        ("Pricing", {"fields": ("price", "compare_at_price", "cost_price", "tax_category")}),
        ("Logistics", {"fields": ("weight_grams", "packaging_type", "low_stock_threshold")}),
        ("Merchandising", {"fields": ("badges",)}),
        ("Food metadata", {"fields": ("food_metadata",), "classes": ("collapse",)}),
        ("Honey metadata", {"fields": ("honey_metadata",), "classes": ("collapse",)}),
        ("SEO", {"fields": ("seo_title", "seo_description"), "classes": ("collapse",)}),
    )


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ["__str__", "sku", "product", "price", "is_active", "is_default"]
    search_fields = ["sku", "barcode", "product__name_en"]
    autocomplete_fields = ["product"]
    inlines = [VariantAttributeValueInline]


@admin.register(MixMatchBox)
class MixMatchBoxAdmin(admin.ModelAdmin):
    list_display = ["product", "minimum_items", "maximum_items", "fixed_price"]
    filter_horizontal = ["eligible_variants"]
