import django_filters
from django.db.models import F, Q

from apps.catalog.models import Product


class ProductFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(field_name="category__slug")
    brand = django_filters.CharFilter(field_name="brand__slug")
    min_price = django_filters.NumberFilter(field_name="price", lookup_expr="gte")
    max_price = django_filters.NumberFilter(field_name="price", lookup_expr="lte")
    organic = django_filters.BooleanFilter(method="filter_organic")
    origin = django_filters.CharFilter(method="filter_origin")
    attribute = django_filters.CharFilter(method="filter_attribute", help_text="attribute_code:value_slug")
    on_sale = django_filters.BooleanFilter(method="filter_on_sale")
    featured = django_filters.BooleanFilter(field_name="featured")

    class Meta:
        model = Product
        fields = ["category", "brand", "min_price", "max_price", "product_type"]

    def filter_organic(self, queryset, name, value):
        if value:
            return queryset.filter(Q(honey_metadata__organic=True) | Q(food_metadata__organic=True))
        return queryset

    def filter_origin(self, queryset, name, value):
        return queryset.filter(Q(honey_metadata__origin__iexact=value) | Q(food_metadata__country_of_origin__iexact=value))

    def filter_attribute(self, queryset, name, value):
        try:
            code, value_slug = value.split(":", 1)
        except ValueError:
            return queryset.none()
        return queryset.filter(
            Q(attribute_values__attribute__code=code, attribute_values__slug=value_slug)
            | Q(variants__attribute_values__attribute__code=code, variants__attribute_values__slug=value_slug)
        ).distinct()

    def filter_on_sale(self, queryset, name, value):
        if value:
            return queryset.filter(compare_at_price__isnull=False, price__lt=F("compare_at_price"))
        return queryset
