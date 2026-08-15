from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.views import APIView

from apps.catalog.filters import ProductFilter
from apps.catalog.models import Product, ProductStatus
from apps.catalog.serializers import ProductDetailSerializer, ProductListSerializer
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response

PRODUCT_LIST_PREFETCH = ["images", "variants__attribute_values__attribute", "variants__images", "attribute_values__attribute"]


def _published_products():
    return (
        Product.objects.filter(status=ProductStatus.ACTIVE, published=True)
        .select_related("brand", "category")
        .prefetch_related(*PRODUCT_LIST_PREFETCH)
    )


@extend_schema(tags=["Products"])
class ProductListView(ListAPIView):
    serializer_class = ProductListSerializer
    permission_classes = [permissions.AllowAny]
    filterset_class = ProductFilter
    search_fields = ["name_en", "name_ar", "sku", "barcode", "description"]
    ordering_fields = ["price", "created_at", "name_en"]
    ordering = ["-created_at"]
    throttle_scope = "search"

    def get_queryset(self):
        return _published_products()

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


@extend_schema(tags=["Products"])
class ProductDetailView(RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"

    def get_queryset(self):
        return _published_products()

    def retrieve(self, request, *args, **kwargs):
        product = self.get_object()
        _track_recently_viewed(request, product)
        return success_response(self.get_serializer(product).data)


def _track_recently_viewed(request, product):
    from apps.catalog.models import RecentlyViewed

    if not request.session.session_key:
        request.session.save()
    user = request.user if request.user.is_authenticated else None
    RecentlyViewed.objects.update_or_create(
        user=user,
        session_key=None if user else request.session.session_key,
        product=product,
        defaults={},
    )
    RecentlyViewed.trim_history(user=user, session_key=None if user else request.session.session_key)


@extend_schema(tags=["Products"])
class RelatedProductsView(APIView):
    """Rule-based: same category, excluding self, featured first. Architecture
    supports swapping in ML-based recommendations later without changing the
    endpoint contract (spec §27)."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, slug):
        product = _published_products().filter(slug=slug).first()
        if not product:
            raise ApplicationError(ErrorCodes.PRODUCT_NOT_FOUND, "Product not found.")
        related = (
            _published_products()
            .filter(category=product.category)
            .exclude(pk=product.pk)
            .order_by("-featured", "-created_at")[:12]
        )
        return success_response(ProductListSerializer(related, many=True).data)


@extend_schema(tags=["Products"])
class RecentlyViewedView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from apps.catalog.models import RecentlyViewed

        user = request.user if request.user.is_authenticated else None
        session_key = None if user else request.session.session_key
        qs = RecentlyViewed.objects.filter(user=user) if user else RecentlyViewed.objects.filter(session_key=session_key)
        product_ids = list(qs.order_by("-viewed_at").values_list("product_id", flat=True)[:20])
        products = _published_products().filter(id__in=product_ids)
        products_by_id = {p.id: p for p in products}
        ordered = [products_by_id[pid] for pid in product_ids if pid in products_by_id]
        return success_response(ProductListSerializer(ordered, many=True).data)
