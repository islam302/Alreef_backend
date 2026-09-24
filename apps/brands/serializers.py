from rest_framework import serializers

from apps.brands.models import Brand


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug", "description", "logo", "website", "country_of_origin"]
