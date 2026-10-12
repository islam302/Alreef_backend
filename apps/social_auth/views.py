from django.contrib.auth.models import update_last_login
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.response import success_response
from apps.notifications.tasks import send_email_task
from apps.social_auth import services
from apps.social_auth.models import SocialProvider
from apps.users.serializers import UserSerializer


@extend_schema(tags=["Auth"], responses=inline_serializer("SocialProviders", {"google": serializers.DictField(), "facebook": serializers.DictField()}))
class ProvidersView(APIView):
    """The sign-in buttons the storefront should show, with their public ids."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        return success_response(services.enabled_providers())


class _SocialLoginView(APIView):
    """Verify the provider's token, find or create the account, sign it in.

    Answers exactly like /auth/login/ — {refresh, access, user} — plus
    `created`, so the storefront treats both the same way.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_scope = "auth"
    provider = None
    token_field = None

    def post(self, request):
        token = str(request.data.get(self.token_field) or "")
        uid, email, verified, first, last = services.VERIFIERS[self.provider](token)
        user, created = services.resolve_user(self.provider, uid, email, verified, first, last)

        refresh = RefreshToken.for_user(user)
        update_last_login(None, user)
        if created:
            transaction.on_commit(lambda: send_email_task.delay(
                to=user.email, subject=_("Welcome!"), template_name="welcome",
                context={"first_name": user.first_name}, event="registration", user_id=user.id,
            ))
        return success_response(
            {"refresh": str(refresh), "access": str(refresh.access_token), "user": UserSerializer(user).data, "created": created},
            message="Login successful.",
            status=201 if created else 200,
        )


@extend_schema(tags=["Auth"], request=inline_serializer("GoogleLogin", {"credential": serializers.CharField()}))
class GoogleLoginView(_SocialLoginView):
    provider = SocialProvider.GOOGLE
    token_field = "credential"


@extend_schema(tags=["Auth"], request=inline_serializer("FacebookLogin", {"access_token": serializers.CharField()}))
class FacebookLoginView(_SocialLoginView):
    provider = SocialProvider.FACEBOOK
    token_field = "access_token"
