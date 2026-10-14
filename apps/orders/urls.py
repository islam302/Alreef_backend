from django.urls import path

from apps.orders.views import (
    GuestOrderLookupView,
    OrderCancelView,
    OrderDetailView,
    OrderListView,
    ReturnRequestCreateView,
    ReturnRequestListView,
)

app_name = "orders"

urlpatterns = [
    path("", OrderListView.as_view(), name="list"),
    path("returns/", ReturnRequestListView.as_view(), name="returns-list"),
    # Before the <order_number> routes, which would otherwise swallow it.
    path("lookup/", GuestOrderLookupView.as_view(), name="guest-lookup"),
    path("<str:order_number>/", OrderDetailView.as_view(), name="detail"),
    path("<str:order_number>/cancel/", OrderCancelView.as_view(), name="cancel"),
    path("<str:order_number>/returns/", ReturnRequestCreateView.as_view(), name="returns-create"),
]
