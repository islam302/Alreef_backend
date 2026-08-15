from django.urls import path

from apps.checkout.views import CheckoutCreateView, CheckoutSummaryView

app_name = "checkout"

urlpatterns = [
    path("summary/", CheckoutSummaryView.as_view(), name="summary"),
    path("", CheckoutCreateView.as_view(), name="create"),
]
