from django.urls import path

from apps.carts.views import (
    CartAddItemView,
    CartApplyCouponView,
    CartDetailView,
    CartItemDetailView,
    CartMergeView,
)

app_name = "carts"

urlpatterns = [
    path("", CartDetailView.as_view(), name="detail"),
    path("items/", CartAddItemView.as_view(), name="add-item"),
    path("items/<int:item_id>/", CartItemDetailView.as_view(), name="item-detail"),
    path("coupon/", CartApplyCouponView.as_view(), name="coupon"),
    path("merge/", CartMergeView.as_view(), name="merge"),
]
