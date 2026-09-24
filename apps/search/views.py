from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView

from apps.catalog.filters import ProductFilter
from apps.catalog.models import Product, ProductStatus
from apps.catalog.serializers import ProductListSerializer
from apps.core.error_codes import ApplicationError, ErrorCodes


@extend_schema(
    tags=["Search"],
    parameters=[OpenApiParameter("q", str, OpenApiParameter.QUERY, required=True, description="Search query, e.g. ?q=honey")],
)
class ProductSearchView(ListAPIView):
    """Full-text search across name, description, SKU, barcode, brand, category (spec §28-29)."""

    serializer_class = ProductListSerializer
    permission_classes = [permissions.AllowAny]
    filterset_class = ProductFilter
    ordering_fields = ["price", "created_at", "rank"]
    throttle_scope = "search"

    def get_queryset(self):
        query = self.request.query_params.get("q", "").strip()
        if not query:
            raise ApplicationError(ErrorCodes.VALIDATION_ERROR, "Query parameter 'q' is required.")

        vector = SearchVector("name_en", weight="A") + SearchVector("name_ar", weight="A") + \
            SearchVector("sku", weight="B") + SearchVector("barcode", weight="B") + \
            SearchVector("brand__name", weight="C") + SearchVector("category__name_en", weight="C") + \
            SearchVector("description", weight="D")
        search_query = SearchQuery(query)

        return (
            Product.objects.filter(status=ProductStatus.ACTIVE, published=True)
            .annotate(rank=SearchRank(vector, search_query))
            .filter(rank__gt=0)
            .select_related("brand", "category")
            .prefetch_related("images", "variants")
            .order_by("-rank")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)
