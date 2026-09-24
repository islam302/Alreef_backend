from rest_framework import serializers

from apps.attributes.models import Attribute, AttributeValue


class AttributeValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeValue
        fields = ["id", "value_en", "value_ar", "slug"]


class AttributeSerializer(serializers.ModelSerializer):
    values = AttributeValueSerializer(many=True, read_only=True)

    class Meta:
        model = Attribute
        fields = ["id", "name_en", "name_ar", "code", "input_type", "is_variant_attribute", "is_filterable", "unit", "values"]
