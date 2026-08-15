from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.views import APIView

from apps.carts import services
from apps.carts.serializers import (
    AddCartItemSerializer,
    ApplyCouponSerializer,
    CartSerializer,
    UpdateCartItemSerializer,
)
from apps.core.response import success_response


@extend_schema(tags=["Cart"], responses=CartSerializer)
class CartDetailView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cart = services.get_or_create_cart(request)
        return success_response(CartSerializer(cart).data)

    def delete(self, request):
        cart = services.get_or_create_cart(request)
        services.clear_cart(cart)
        return success_response(CartSerializer(cart).data, message="Cart cleared.")


@extend_schema(tags=["Cart"], request=AddCartItemSerializer, responses=CartSerializer)
class CartAddItemView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AddCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = services.get_or_create_cart(request)
        services.add_item(cart, **serializer.validated_data)
        cart.refresh_from_db()
        return success_response(CartSerializer(cart).data, message="Item added to cart.")


@extend_schema(tags=["Cart"], request=UpdateCartItemSerializer, responses=CartSerializer)
class CartItemDetailView(APIView):
    permission_classes = [permissions.AllowAny]

    def patch(self, request, item_id):
        serializer = UpdateCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = services.get_or_create_cart(request)
        services.update_item_quantity(cart, item_id=item_id, quantity=serializer.validated_data["quantity"])
        cart.refresh_from_db()
        return success_response(CartSerializer(cart).data, message="Cart updated.")

    def delete(self, request, item_id):
        cart = services.get_or_create_cart(request)
        services.remove_item(cart, item_id=item_id)
        cart.refresh_from_db()
        return success_response(CartSerializer(cart).data, message="Item removed.")


@extend_schema(tags=["Cart"], request=ApplyCouponSerializer, responses=CartSerializer)
class CartApplyCouponView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ApplyCouponSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = services.get_or_create_cart(request)

        from apps.coupons.services import validate_coupon_for_cart

        validate_coupon_for_cart(code=serializer.validated_data["code"], cart=cart, user=request.user if request.user.is_authenticated else None)
        cart.coupon_code = serializer.validated_data["code"]
        cart.save(update_fields=["coupon_code"])
        return success_response(CartSerializer(cart).data, message="Coupon applied.")

    def delete(self, request):
        cart = services.get_or_create_cart(request)
        cart.coupon_code = ""
        cart.save(update_fields=["coupon_code"])
        return success_response(CartSerializer(cart).data, message="Coupon removed.")


@extend_schema(tags=["Cart"], responses=CartSerializer)
class CartMergeView(APIView):
    """Called right after login with the anonymous session's cart id, per
    docs/frontend/README.md merge flow (spec §24)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from apps.carts.models import Cart, CartStatus

        guest_cart_id = request.data.get("guest_cart_id")
        if not guest_cart_id:
            cart = services.get_or_create_cart(request)
            return success_response(CartSerializer(cart).data)

        guest_cart = Cart.objects.filter(pk=guest_cart_id, status=CartStatus.OPEN, user__isnull=True).first()
        if not guest_cart:
            cart = services.get_or_create_cart(request)
            return success_response(CartSerializer(cart).data)

        merged = services.merge_guest_cart_into_user(guest_cart=guest_cart, user=request.user)
        return success_response(CartSerializer(merged).data, message="Carts merged.")
