from django.urls import path

from apps.wishlists.views import (
    WishlistAddView,
    WishlistCheckView,
    WishlistListView,
    WishlistRemoveView,
)

app_name = "wishlists"

urlpatterns = [
    path("", WishlistListView.as_view(), name="list"),
    path("add/", WishlistAddView.as_view(), name="add"),
    path("<int:product_id>/", WishlistRemoveView.as_view(), name="remove"),
    path("<int:product_id>/check/", WishlistCheckView.as_view(), name="check"),
]
