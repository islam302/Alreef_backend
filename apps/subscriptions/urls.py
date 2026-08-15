from django.urls import path

from apps.subscriptions.views import (
    SubscriptionCancelView,
    SubscriptionCreateView,
    SubscriptionDetailView,
    SubscriptionListView,
    SubscriptionPauseView,
    SubscriptionResumeView,
)

app_name = "subscriptions"

urlpatterns = [
    path("", SubscriptionListView.as_view(), name="list"),
    path("create/", SubscriptionCreateView.as_view(), name="create"),
    path("<int:pk>/", SubscriptionDetailView.as_view(), name="detail"),
    path("<int:pk>/pause/", SubscriptionPauseView.as_view(), name="pause"),
    path("<int:pk>/resume/", SubscriptionResumeView.as_view(), name="resume"),
    path("<int:pk>/cancel/", SubscriptionCancelView.as_view(), name="cancel"),
]
