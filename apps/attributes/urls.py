from django.urls import path

from apps.attributes.views import AttributeListView

app_name = "attributes"

urlpatterns = [
    path("", AttributeListView.as_view(), name="list"),
]
