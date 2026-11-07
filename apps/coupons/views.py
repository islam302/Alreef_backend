from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.views import APIView

from apps.core.response import success_response
from apps.core.schema import envelope
from apps.coupons import welcome
from apps.coupons.serializers import (
    CouponPublicSerializer,
    WelcomeCouponSerializer,
    WelcomeOfferClaimSerializer,
    WelcomeOfferRequestSerializer,
    WelcomeOfferSerializer,
)
from apps.coupons.services import check_owner, get_valid_coupon


@extend_schema(tags=["Coupons"], responses=envelope(CouponPublicSerializer, name="CouponValidate"))
class CouponValidateView(APIView):
    """Lets the frontend preview a coupon's terms before applying it to the cart."""

    permission_classes = [permissions.AllowAny]
    throttle_scope = "coupon"

    def get(self, request, code):
        coupon = get_valid_coupon(code)
        check_owner(coupon, request.user)
        return success_response(CouponPublicSerializer(coupon).data)


@extend_schema(
    tags=["Coupons"],
    request=WelcomeOfferRequestSerializer,
    responses=envelope(WelcomeOfferSerializer, name="WelcomeOffer"),
)
class WelcomeOfferView(APIView):
    """
    The welcome offer for a visitor's device. Sent with no token, it issues a
    new signed one. Sent with the token the device already holds, it returns
    that token's verified terms — or WELCOME_OFFER_EXPIRED once its 24 hours
    are up, in which case no new offer is issued in its place.
    """

    permission_classes = [permissions.AllowAny]
    throttle_scope = "coupon"

    def post(self, request):
        serializer = WelcomeOfferRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data.get("token")
        if token:
            terms = welcome.read_token(token)
        else:
            terms = welcome.issue_token()
        return success_response(WelcomeOfferSerializer(terms).data)


@extend_schema(
    tags=["Coupons"],
    request=WelcomeOfferClaimSerializer,
    responses=envelope(WelcomeCouponSerializer, name="WelcomeCoupon"),
)
class WelcomeOfferClaimView(APIView):
    """
    GET: the signed-in account's welcome coupon, while it is unused and
    unexpired, or null. POST: turn a device's offer token into that
    coupon. Idempotent for an account that already holds a live one.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "coupon"

    def get(self, request):
        coupon = welcome.current_coupon(request.user)
        return success_response(WelcomeCouponSerializer(coupon).data if coupon else None)

    def post(self, request):
        serializer = WelcomeOfferClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        coupon = welcome.claim(user=request.user, token=serializer.validated_data["token"])
        return success_response(WelcomeCouponSerializer(coupon).data, message="Welcome offer claimed.")
