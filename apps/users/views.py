from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView as BaseTokenRefreshView

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.permissions import IsOwner
from apps.core.response import success_response
from apps.notifications.tasks import send_email_task, send_sms_task
from apps.users import services
from apps.users.models import Address
from apps.users.serializers import (
    AddressSerializer,
    ChangePasswordSerializer,
    EmailVerifyConfirmSerializer,
    EmailVerifyRequestSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PhoneVerifyConfirmSerializer,
    PhoneVerifyRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)
from apps.users.models import User
from rest_framework_simplejwt.views import TokenObtainPairView


@extend_schema(tags=["Auth"], request=RegisterSerializer, responses=UserSerializer)
class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        token = services.issue_email_verification_token(user)
        verify_url = f"{request._request.build_absolute_uri('/')[:-1]}/api/v1/auth/verify-email/confirm/?token={token}"
        send_email_task.delay(
            to=user.email, subject=_("Verify your email"), template_name="verify_email",
            context={"verify_url": verify_url, "first_name": user.first_name}, event="email_verification", user_id=user.id,
        )
        send_email_task.delay(
            to=user.email, subject=_("Welcome!"), template_name="welcome",
            context={"first_name": user.first_name}, event="registration", user_id=user.id,
        )
        return success_response(UserSerializer(user).data, message="Account created. Please verify your email.", status=201)


@extend_schema(tags=["Auth"], request=LoginSerializer)
class LoginView(TokenObtainPairView):
    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer
    throttle_scope = "auth"

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return success_response(serializer.validated_data, message="Login successful.")


@extend_schema(tags=["Auth"], request=LogoutSerializer)
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            token.blacklist()
        except TokenError as exc:
            raise ApplicationError(ErrorCodes.AUTH_TOKEN_INVALID, "Invalid or already-blacklisted refresh token.") from exc
        return success_response(message="Logged out.")


@extend_schema(tags=["Auth"])
class TokenRefreshView(BaseTokenRefreshView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth"

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise ApplicationError(ErrorCodes.AUTH_TOKEN_EXPIRED, "Refresh token is invalid or expired.") from exc
        return success_response(serializer.validated_data, message="Token refreshed.")


@extend_schema(tags=["Auth"])
class MeView(RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch"]

    def get_object(self):
        return self.request.user

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)

    def patch(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(serializer.data, message="Profile updated.")


@extend_schema(tags=["Auth"], request=ChangePasswordSerializer)
class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth_confirm"

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        return success_response(message="Password changed.")


@extend_schema(tags=["Auth"], request=PasswordResetRequestSerializer)
class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email=serializer.validated_data["email"].lower()).first()
        if user:
            token = services.issue_password_reset_token(user)
            reset_url = f"{request.data.get('frontend_reset_url', '')}?token={token}" if request.data.get("frontend_reset_url") else f"?token={token}"
            send_email_task.delay(
                to=user.email, subject=_("Reset your password"), template_name="reset_password",
                context={"reset_url": reset_url}, event="password_reset", user_id=user.id,
            )
        # Always return success — never leak which emails are registered.
        return success_response(message="If that email exists, a reset link has been sent.")


@extend_schema(tags=["Auth"], request=PasswordResetConfirmSerializer)
class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth_confirm"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirm_password_reset(serializer.validated_data["token"], serializer.validated_data["new_password"])
        return success_response(message="Password has been reset.")


@extend_schema(tags=["Auth"], request=EmailVerifyRequestSerializer)
class EmailVerifyRequestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request):
        if request.user.email_verified:
            return success_response(message="Email already verified.")
        token = services.issue_email_verification_token(request.user)
        verify_url = f"?token={token}"
        send_email_task.delay(
            to=request.user.email, subject=_("Verify your email"), template_name="verify_email",
            context={"verify_url": verify_url}, event="email_verification", user_id=request.user.id,
        )
        return success_response(message="Verification email sent.")


@extend_schema(tags=["Auth"], request=EmailVerifyConfirmSerializer)
class EmailVerifyConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth_confirm"

    def post(self, request):
        token = request.data.get("token") or request.query_params.get("token")
        serializer = EmailVerifyConfirmSerializer(data={"token": token})
        serializer.is_valid(raise_exception=True)
        services.confirm_email_verification_token(serializer.validated_data["token"])
        return success_response(message="Email verified.")


@extend_schema(tags=["Auth"], request=PhoneVerifyRequestSerializer)
class PhoneVerifyRequestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request):
        serializer = PhoneVerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data.get("phone") or str(request.user.phone or "")
        if not phone:
            raise ApplicationError(ErrorCodes.VALIDATION_ERROR, "No phone number on file. Provide one.")
        code = services.issue_phone_otp(request.user)
        send_sms_task.delay(to=phone, message=f"Your verification code is {code}", event="phone_verification", user_id=request.user.id)
        return success_response(message="OTP sent.")


@extend_schema(tags=["Auth"], request=PhoneVerifyConfirmSerializer)
class PhoneVerifyConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth_confirm"

    def post(self, request):
        serializer = PhoneVerifyConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirm_phone_otp(request.user, serializer.validated_data["code"])
        return success_response(message="Phone verified.")


@extend_schema(tags=["Addresses"])
class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user).select_related("governorate", "city")

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return success_response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(serializer.data, message="Address created.", status=201)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(serializer.data, message="Address updated.")

    def destroy(self, request, *args, **kwargs):
        self.get_object().delete()
        return success_response(message="Address deleted.", status=204)

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        address = self.get_object()
        address.is_default = True
        address.save(update_fields=["is_default"])
        return success_response(self.get_serializer(address).data, message="Default address updated.")
