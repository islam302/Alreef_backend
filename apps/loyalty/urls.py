from django.urls import path

from apps.loyalty.views import (
    LoyaltyAccountView,
    LoyaltyRedeemView,
    LoyaltyTransactionListView,
)

app_name = "loyalty"

urlpatterns = [
    path("", LoyaltyAccountView.as_view(), name="account"),
    path("transactions/", LoyaltyTransactionListView.as_view(), name="transactions"),
    path("redeem/", LoyaltyRedeemView.as_view(), name="redeem"),
]
