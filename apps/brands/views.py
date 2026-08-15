from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView, RetrieveAPIView

from apps.brands.models import Brand
from apps.brands.serializers import BrandSerializer
from apps.core.response import success_response


@extend_schema(tags=["Brands"])
class BrandListView(ListAPIView):
    queryset = Brand.objects.filter(is_active=True)
    serializer_class = BrandSerializer
    permission_classes = [permissions.AllowAny]
    search_fields = ["name"]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema(tags=["Brands"])
class BrandDetailView(RetrieveAPIView):
    queryset = Brand.objects.filter(is_active=True)
    serializer_class = BrandSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)
