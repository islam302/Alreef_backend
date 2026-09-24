from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.views import APIView

from apps.categories.models import Category
from apps.categories.serializers import (
    CategoryDetailSerializer,
    CategoryListSerializer,
    CategoryTreeSerializer,
)
from apps.core.response import success_response
from apps.core.schema import envelope


@extend_schema(tags=["Categories"])
class CategoryListView(ListAPIView):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategoryListSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    filterset_fields = ["parent"]
    search_fields = ["name_en", "name_ar"]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        return success_response(self.get_serializer(queryset, many=True).data)


@extend_schema(tags=["Categories"], responses=envelope(CategoryTreeSerializer, many=True, name="CategoryTree"))
class CategoryTreeView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        roots = Category.objects.filter(is_active=True, parent__isnull=True).order_by("sort_order", "name_en")
        return success_response(CategoryTreeSerializer(roots, many=True).data)


@extend_schema(tags=["Categories"])
class CategoryDetailView(RetrieveAPIView):
    queryset = Category.objects.filter(is_active=True)
    serializer_class = CategoryDetailSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)
