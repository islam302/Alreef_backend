"""
Aggregates all /api/v1/ routes. Each app owns its own urls.py.
"""
from django.urls import include, path

urlpatterns = [
    path("auth/", include("apps.users.urls_auth")),
    path("", include("apps.users.urls")),
    path("categories/", include("apps.categories.urls")),
    path("brands/", include("apps.brands.urls")),
    path("attributes/", include("apps.attributes.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.search.urls")),
    path("cart/", include("apps.carts.urls")),
    path("wishlist/", include("apps.wishlists.urls")),
    path("", include("apps.reviews.urls")),
    path("coupons/", include("apps.coupons.urls")),
    path("promotions/", include("apps.promotions.urls")),
    path("shipping/", include("apps.shipping.urls")),
    path("checkout/", include("apps.checkout.urls")),
    path("orders/", include("apps.orders.urls")),
    path("payments/", include("apps.payments.urls")),
    path("loyalty/", include("apps.loyalty.urls")),
    path("gift-cards/", include("apps.gift_cards.urls")),
    path("subscriptions/", include("apps.subscriptions.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("content/", include("apps.content.urls")),
    path("admin-api/", include("apps.analytics.urls")),
    path("admin-api/", include("apps.admin_api.urls")),
    path("admin-api/", include("apps.content.urls_admin")),
]
