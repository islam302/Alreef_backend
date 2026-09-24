from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView

from apps.core.response import success_response
from apps.gift_cards import services
from apps.gift_cards.models import StoreCreditTransaction
from apps.gift_cards.serializers import (
    GiftCardCheckSerializer,
    GiftCardPublicSerializer,
    StoreCreditAccountSerializer,
    StoreCreditTransactionSerializer,
)


@extend_schema(tags=["Gift Cards"], request=GiftCardCheckSerializer)
class GiftCardCheckView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "gift_card"

    def post(self, request):
        serializer = GiftCardCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gift_card = services.get_valid_gift_card(serializer.validated_data["code"])
        return success_response(GiftCardPublicSerializer(gift_card).data)


@extend_schema(tags=["Gift Cards"])
class StoreCreditAccountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        account = services.get_or_create_store_credit_account(request.user)
        return success_response(StoreCreditAccountSerializer(account).data)


@extend_schema(tags=["Gift Cards"])
class StoreCreditTransactionListView(ListAPIView):
    serializer_class = StoreCreditTransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        account = services.get_or_create_store_credit_account(self.request.user)
        return StoreCreditTransaction.objects.filter(account=account)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)
