from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Product
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.core.schema import envelope
from apps.wishlists.models import WishlistItem
from apps.wishlists.serializers import WishlistAddSerializer, WishlistItemSerializer


@extend_schema(tags=["Wishlist"])
class WishlistListView(ListAPIView):
    serializer_class = WishlistItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return WishlistItem.objects.filter(user=self.request.user).select_related("product")

    def list(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_queryset(), many=True).data)


@extend_schema(tags=["Wishlist"], request=WishlistAddSerializer, responses=envelope(WishlistItemSerializer, name="WishlistAdd"))
class WishlistAddView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = WishlistAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = Product.objects.filter(pk=serializer.validated_data["product_id"]).first()
        if not product:
            raise ApplicationError(ErrorCodes.PRODUCT_NOT_FOUND, "Product not found.")
        item, _ = WishlistItem.objects.get_or_create(user=request.user, product=product)
        return success_response(WishlistItemSerializer(item).data, message="Added to wishlist.", status=201)


@extend_schema(tags=["Wishlist"], responses=envelope(name="WishlistRemove"))
class WishlistRemoveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, product_id):
        deleted, _ = WishlistItem.objects.filter(user=request.user, product_id=product_id).delete()
        if not deleted:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Item not in wishlist.")
        # A 204 carries no body; sending one breaks proxies that read the next
        # response off the same connection.
        return Response(status=204)


@extend_schema(tags=["Wishlist"], responses=envelope(name="WishlistCheck"))
class WishlistCheckView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, product_id):
        exists = WishlistItem.objects.filter(user=request.user, product_id=product_id).exists()
        return success_response({"in_wishlist": exists})
