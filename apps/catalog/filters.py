import django_filters
from django.db.models import F, Q

from apps.catalog.models import Product, ProductBadgeCode

# A curated shelf (a homepage section the shop sets in the dashboard) names
# its products by slug. Bounded so a crafted URL cannot ask for the catalogue.
MAX_SLUGS = 24

# Only badges staff set by hand can be filtered on; OUT_OF_STOCK / LOW_STOCK
# are computed per request and are never stored.
STORED_BADGES = {code for code, _ in ProductBadgeCode.choices}


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
    badge = django_filters.CharFilter(method="filter_badge", help_text="A stored badge code, e.g. BEST_SELLER")
    slugs = django_filters.CharFilter(method="filter_slugs", help_text=f"Comma-separated slugs, at most {MAX_SLUGS}")

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

    def filter_badge(self, queryset, name, value):
        code = value.strip().upper()
        if code not in STORED_BADGES:
            return queryset.none()
        return queryset.filter(badges__contains=[code])

    def filter_slugs(self, queryset, name, value):
        slugs = [s.strip() for s in value.split(",") if s.strip()][:MAX_SLUGS]
        if not slugs:
            return queryset.none()
        return queryset.filter(slug__in=slugs)
