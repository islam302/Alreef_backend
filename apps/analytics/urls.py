from django.urls import path

from apps.analytics.views import AdminDashboardView, TrackEventView

app_name = "analytics"

urlpatterns = [
    path("events/track/", TrackEventView.as_view(), name="track"),
    path("dashboard/", AdminDashboardView.as_view(), name="dashboard"),
]
