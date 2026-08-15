from rest_framework.routers import DefaultRouter

from apps.users.views import AddressViewSet

app_name = "users"

router = DefaultRouter()
router.register("addresses", AddressViewSet, basename="address")

urlpatterns = router.urls
