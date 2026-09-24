from django.urls import path

from apps.promotions.views import ActivePromotionListView

app_name = "promotions"

urlpatterns = [
    path("", ActivePromotionListView.as_view(), name="list"),
]
