from django.urls import path

from apps.notifications.views import (
    NotificationListView,
    NotificationMarkReadView,
    NotificationPreferenceView,
)

app_name = "notifications"

urlpatterns = [
    path("", NotificationListView.as_view(), name="list"),
    path("<int:pk>/read/", NotificationMarkReadView.as_view(), name="mark-read"),
    path("preferences/", NotificationPreferenceView.as_view(), name="preferences"),
]
