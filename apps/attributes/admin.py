from django.contrib import admin

from apps.attributes.models import Attribute, AttributeValue


class AttributeValueInline(admin.TabularInline):
    model = AttributeValue
    extra = 1
    prepopulated_fields = {"slug": ("value_en",)}


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ["name_en", "code", "input_type", "is_variant_attribute", "is_filterable"]
    list_filter = ["input_type", "is_variant_attribute", "is_filterable"]
    search_fields = ["name_en", "name_ar", "code"]
    inlines = [AttributeValueInline]


@admin.register(AttributeValue)
class AttributeValueAdmin(admin.ModelAdmin):
    list_display = ["value_en", "attribute", "slug"]
    list_filter = ["attribute"]
    search_fields = ["value_en", "value_ar", "slug"]
