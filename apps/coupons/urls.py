from django.urls import path

from apps.coupons.views import CouponValidateView

app_name = "coupons"

urlpatterns = [
    path("<str:code>/", CouponValidateView.as_view(), name="validate"),
]
