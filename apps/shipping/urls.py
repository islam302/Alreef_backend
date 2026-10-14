from django.urls import path

from apps.shipping.views import (
    CityListView,
    GovernorateListView,
    OrderShipmentsView,
    ShipmentTrackingView,
    ShippingQuoteView,
)

app_name = "shipping"

urlpatterns = [
    path("governorates/", GovernorateListView.as_view(), name="governorates"),
    path("cities/", CityListView.as_view(), name="cities"),
    path("quotes/", ShippingQuoteView.as_view(), name="quotes"),
    path("track/<str:tracking_number>/", ShipmentTrackingView.as_view(), name="track"),
    path("orders/<str:order_number>/shipments/", OrderShipmentsView.as_view(), name="order-shipments"),
]
