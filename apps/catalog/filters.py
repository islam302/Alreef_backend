import django_filters
from django.db.models import F, Q

from apps.catalog.models import Product, ProductBadgeCode
from apps.categories.models import Category

# A curated shelf (a homepage section the shop sets in the dashboard) names
# its products by slug. Bounded so a crafted URL cannot ask for the catalogue.
MAX_SLUGS = 24

# Only badges staff set by hand can be filtered on; OUT_OF_STOCK / LOW_STOCK
# are computed per request and are never stored.
STORED_BADGES = {code for code, _ in ProductBadgeCode.choices}

# A product has exactly one category (a FK), so "offers" cannot be a shelf the
# products are moved onto: that would take a discounted Sidr jar out of
# Sidr honey. `?category=offers` is resolved instead: everything on sale
# right now (the same rule as `?on_sale=true`), plus anything the shop did
# file under an `offers` category.
OFFERS_SLUG = "offers"


def on_sale_q() -> Q:
    return Q(compare_at_price__isnull=False, price__lt=F("compare_at_price"))


def category_subtree_q(category) -> Q:
    """The category and every category under it (MPTT nested set, one join)."""
    return Q(
        category__tree_id=category.tree_id,
        category__lft__gte=category.lft,
        category__rght__lte=category.rght,
    )


class ProductFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(
        method="filter_category",
        help_text="A category slug; includes its sub-categories. `offers` lists what is on sale.",
    )
    brand = django_filters.CharFilter(field_name="brand__slug")
    min_price = django_filters.NumberFilter(field_name="price", lookup_expr="gte")
    max_price = django_filters.NumberFilter(field_name="price", lookup_expr="lte")
    organic = django_filters.BooleanFilter(method="filter_organic")
    origin = django_filters.CharFilter(method="filter_origin")
    attribute = django_filters.CharFilter(method="filter_attribute", help_text="attribute_code:value_slug")
    on_sale = django_filters.BooleanFilter(method="filter_on_sale")
    featured = django_filters.BooleanFilter(field_name="featured")
    badge = django_filters.CharFilter(method="filter_badge", help_text="A stored badge code, e.g. BEST_SELLER")
    best_sellers = django_filters.BooleanFilter(
        method="filter_best_sellers",
        help_text="Products that have sold at least one jar, plus any the shop marked BEST_SELLER. "
        "Pair with ordering=-units_sold.",
    )
    slugs = django_filters.CharFilter(method="filter_slugs", help_text=f"Comma-separated slugs, at most {MAX_SLUGS}")

    class Meta:
        model = Product
        fields = ["category", "brand", "min_price", "max_price", "product_type"]

    def filter_category(self, queryset, name, value):
        slug = value.strip()
        category = Category.objects.filter(slug=slug).first()
        if slug == OFFERS_SLUG:
            q = on_sale_q()
            if category is not None:
                q |= category_subtree_q(category)
            return queryset.filter(q)
        if category is None:
            return queryset.none()
        return queryset.filter(category_subtree_q(category))

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
            return queryset.filter(on_sale_q())
        return queryset

    def filter_badge(self, queryset, name, value):
        code = value.strip().upper()
        if code not in STORED_BADGES:
            return queryset.none()
        return queryset.filter(badges__contains=[code])

    def filter_best_sellers(self, queryset, name, value):
        # `units_sold` is annotated by the storefront queryset (_published_products).
        if value:
            return queryset.filter(Q(units_sold__gt=0) | Q(badges__contains=["BEST_SELLER"]))
        return queryset

    def filter_slugs(self, queryset, name, value):
        slugs = [s.strip() for s in value.split(",") if s.strip()][:MAX_SLUGS]
        if not slugs:
            return queryset.none()
        return queryset.filter(slug__in=slugs)
