from django.urls import path

from apps.coupons.views import CouponValidateView, WelcomeOfferClaimView, WelcomeOfferView

app_name = "coupons"

# The welcome routes come first: "<str:code>/" would otherwise swallow them.
urlpatterns = [
    path("welcome-offer/", WelcomeOfferView.as_view(), name="welcome-offer"),
    path("welcome-offer/claim/", WelcomeOfferClaimView.as_view(), name="welcome-offer-claim"),
    path("<str:code>/", CouponValidateView.as_view(), name="validate"),
]
