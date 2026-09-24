from django.urls import path

from apps.search.views import ProductSearchView

app_name = "search"

urlpatterns = [
    path("search/", ProductSearchView.as_view(), name="search"),
]
