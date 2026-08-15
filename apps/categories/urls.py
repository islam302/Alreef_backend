from django.urls import path

from apps.categories.views import CategoryDetailView, CategoryListView, CategoryTreeView

app_name = "categories"

urlpatterns = [
    path("", CategoryListView.as_view(), name="list"),
    path("tree/", CategoryTreeView.as_view(), name="tree"),
    path("<slug:slug>/", CategoryDetailView.as_view(), name="detail"),
]
