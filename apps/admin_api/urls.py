"""
Staff API routes. Mounted at /api/v1/admin-api/ alongside apps.analytics,
which keeps ownership of `dashboard/` and the public `events/track/`.
"""
from django.urls import include, path
from rest_framework.permissions import IsAdminUser
from rest_framework.routers import APIRootView, DefaultRouter

from apps.admin_api import views

app_name = "admin_api"


class StaffAPIRootView(APIRootView):
    """The router's own index is a view like any other, and on the DRF
    default it answered anonymously with the name and URL of every staff
    route — a free map of the back office for anyone who typed the prefix."""

    permission_classes = [IsAdminUser]


class StaffRouter(DefaultRouter):
    APIRootView = StaffAPIRootView


router = StaffRouter()
router.register("orders", views.AdminOrderViewSet, basename="admin-order")
router.register("returns", views.AdminReturnViewSet, basename="admin-return")
router.register("products", views.AdminProductViewSet, basename="admin-product")
router.register("product-images", views.AdminProductImageViewSet, basename="admin-product-image")
router.register("variants", views.AdminProductVariantViewSet, basename="admin-variant")
router.register("categories", views.AdminCategoryViewSet, basename="admin-category")
router.register("customers", views.AdminCustomerViewSet, basename="admin-customer")
router.register("inventory", views.AdminInventoryViewSet, basename="admin-inventory")
router.register("inventory-movements", views.AdminInventoryMovementViewSet, basename="admin-inventory-movement")
router.register("batches", views.AdminBatchViewSet, basename="admin-batch")
router.register("coupons", views.AdminCouponViewSet, basename="admin-coupon")
router.register("reviews", views.AdminReviewViewSet, basename="admin-review")
router.register("store-reviews", views.AdminStoreReviewViewSet, basename="admin-store-review")
router.register("payments", views.AdminPaymentViewSet, basename="admin-payment")
router.register("audit-logs", views.AdminAuditLogViewSet, basename="admin-audit-log")
router.register("staff", views.AdminStaffViewSet, basename="admin-staff")
router.register("shipping/governorates", views.AdminShippingGovernorateViewSet, basename="admin-shipping-governorate")
router.register("shipping/zones", views.AdminShippingZoneViewSet, basename="admin-shipping-zone")
router.register("shipping/rates", views.AdminShippingRateViewSet, basename="admin-shipping-rate")
router.register("shipping/methods", views.AdminShippingMethodViewSet, basename="admin-shipping-method")

urlpatterns = [
    path("me/", views.AdminMeView.as_view(), name="me"),
    path("overview/", views.AdminOverviewView.as_view(), name="overview"),
    path("sales-chart/", views.AdminSalesChartView.as_view(), name="sales-chart"),
    # Staff-only view of which payment methods are live and what is missing from
    # the rest. The public /payments/methods/ never says why.
    path("payment-methods/", views.AdminPaymentMethodsView.as_view(), name="payment-methods"),
    path("", include(router.urls)),
]
