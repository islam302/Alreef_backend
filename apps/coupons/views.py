from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.views import APIView

from apps.core.response import success_response
from apps.coupons.serializers import CouponPublicSerializer
from apps.coupons.services import get_valid_coupon


@extend_schema(tags=["Coupons"])
class CouponValidateView(APIView):
    """Lets the frontend preview a coupon's terms before applying it to the cart."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, code):
        coupon = get_valid_coupon(code)
        return success_response(CouponPublicSerializer(coupon).data)
