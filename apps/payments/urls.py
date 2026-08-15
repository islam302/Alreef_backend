from django.urls import path

from apps.payments.views import (
    PaymentDetailView,
    PaymentStatusView,
    PaymentWebhookView,
    RefundCreateView,
)

app_name = "payments"

urlpatterns = [
    path("<int:pk>/", PaymentDetailView.as_view(), name="detail"),
    path("<int:pk>/status/", PaymentStatusView.as_view(), name="status"),
    path("<int:pk>/refund/", RefundCreateView.as_view(), name="refund"),
    path("webhooks/<str:provider>/", PaymentWebhookView.as_view(), name="webhook"),
]
