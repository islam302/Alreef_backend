from django.urls import path

from apps.social_auth import views

app_name = "social_auth"

urlpatterns = [
    path("providers/", views.ProvidersView.as_view(), name="providers"),
    path("google/", views.GoogleLoginView.as_view(), name="google"),
    path("facebook/", views.FacebookLoginView.as_view(), name="facebook"),
]
