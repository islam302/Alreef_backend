from django.urls import path

from apps.users import views

app_name = "auth"

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("token/refresh/", views.TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", views.MeView.as_view(), name="me"),
    path("change-password/", views.ChangePasswordView.as_view(), name="change-password"),
    path("password-reset/", views.PasswordResetRequestView.as_view(), name="password-reset"),
    path("password-reset-confirm/", views.PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("verify-email/", views.EmailVerifyRequestView.as_view(), name="verify-email"),
    path("verify-email/confirm/", views.EmailVerifyConfirmView.as_view(), name="verify-email-confirm"),
    path("verify-phone/", views.PhoneVerifyRequestView.as_view(), name="verify-phone"),
    path("verify-phone/confirm/", views.PhoneVerifyConfirmView.as_view(), name="verify-phone-confirm"),
]
