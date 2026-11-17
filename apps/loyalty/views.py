from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.core.schema import envelope
from apps.loyalty import services
from apps.loyalty.models import LoyaltyTransaction
from apps.loyalty.serializers import (
    LoyaltyAccountSerializer,
    LoyaltyRedeemSerializer,
    LoyaltyTransactionSerializer,
)


@extend_schema(tags=["Loyalty"], responses=envelope(LoyaltyAccountSerializer, name="LoyaltyAccount"))
class LoyaltyAccountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        account = services.get_or_create_account(request.user)
        return success_response(LoyaltyAccountSerializer(account).data)


@extend_schema(tags=["Loyalty"])
class LoyaltyTransactionListView(ListAPIView):
    serializer_class = LoyaltyTransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        account = services.get_or_create_account(self.request.user)
        return LoyaltyTransaction.objects.filter(account=account)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema(tags=["Loyalty"], request=LoyaltyRedeemSerializer, responses=envelope(name="LoyaltyRedeem"))
class LoyaltyRedeemView(APIView):
    """Preview/redeem points into a store-credit-equivalent discount value.
    Applying it to an order's checkout total is a frontend-selected step at
    checkout (see docs/api/checkout.md)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LoyaltyRedeemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Refused until checkout can apply the discount. This took the points
        # off the balance and handed back a number nothing would ever accept,
        # so every redemption simply lost the customer their points.
        raise ApplicationError(
            ErrorCodes.LOYALTY_REDEMPTION_UNAVAILABLE,
            "Redeeming points is not available yet. Your points are safe.",
        )
