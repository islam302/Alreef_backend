from django.urls import path

from apps.gift_cards.views import (
    GiftCardCheckView,
    StoreCreditAccountView,
    StoreCreditTransactionListView,
)

app_name = "gift_cards"

urlpatterns = [
    path("check/", GiftCardCheckView.as_view(), name="check"),
    path("store-credit/", StoreCreditAccountView.as_view(), name="store-credit"),
    path("store-credit/transactions/", StoreCreditTransactionListView.as_view(), name="store-credit-transactions"),
]
