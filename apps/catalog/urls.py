from django.urls import path

from apps.catalog.views import (
    ProductDetailView,
    ProductListView,
    RecentlyViewedView,
    RelatedProductsView,
)

app_name = "catalog"

urlpatterns = [
    path("products/", ProductListView.as_view(), name="list"),
    path("products/recently-viewed/", RecentlyViewedView.as_view(), name="recently-viewed"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="detail"),
    path("products/<slug:slug>/related/", RelatedProductsView.as_view(), name="related"),
]
