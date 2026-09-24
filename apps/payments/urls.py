from django.urls import path

from apps.payments.views import (
    ManualSettlementView,
    PaymentDetailView,
    PaymentMethodsView,
    PaymentReconcileView,
    PaymentStatusView,
    PaymentWebhookView,
    RefundCreateView,
)

app_name = "payments"

urlpatterns = [
    # Public. Must come before "<int:pk>/" so "methods" is not read as an id.
    path("methods/", PaymentMethodsView.as_view(), name="methods"),
    path("webhooks/<str:provider>/", PaymentWebhookView.as_view(), name="webhook"),
    # Customer-facing.
    path("<int:pk>/", PaymentDetailView.as_view(), name="detail"),
    path("<int:pk>/status/", PaymentStatusView.as_view(), name="status"),
    # Staff.
    path("<int:pk>/refund/", RefundCreateView.as_view(), name="refund"),
    path("<int:pk>/reconcile/", PaymentReconcileView.as_view(), name="reconcile"),
    # Cash on delivery and InstaPay: a person confirms the money arrived.
    path("<int:pk>/settle/", ManualSettlementView.as_view(), name="settle"),
]
