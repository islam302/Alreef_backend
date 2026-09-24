from rest_framework import serializers

from apps.categories.models import Category


class CategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name_en", "name_ar", "slug", "image", "icon", "parent"]


class CategoryTreeSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name_en", "name_ar", "slug", "image", "icon", "children"]

    def get_children(self, obj):
        children = obj.get_children().filter(is_active=True)
        return CategoryTreeSerializer(children, many=True, context=self.context).data


class CategoryDetailSerializer(serializers.ModelSerializer):
    breadcrumbs = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id", "name_en", "name_ar", "slug", "description", "parent", "image", "icon",
            "seo_title", "seo_description", "breadcrumbs",
        ]

    def get_breadcrumbs(self, obj):
        return [{"id": a.id, "name_en": a.name_en, "slug": a.slug} for a in obj.get_ancestors(include_self=True)]
