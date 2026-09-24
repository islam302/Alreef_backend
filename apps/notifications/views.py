from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.views import APIView

from apps.core.response import success_response
from apps.core.schema import envelope
from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.serializers import NotificationPreferenceSerializer, NotificationSerializer


@extend_schema(tags=["Notifications"])
class NotificationListView(ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


@extend_schema(
    # POST with no body: the action is the URL.
    request=None,
    tags=["Notifications"], responses=envelope(NotificationSerializer, name="NotificationMarkRead"),
)
class NotificationMarkReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        notification = Notification.objects.filter(pk=pk, user=request.user).first()
        if not notification:
            return success_response(message="Not found", status=status.HTTP_404_NOT_FOUND)
        if not notification.read_at:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return success_response(NotificationSerializer(notification).data)


@extend_schema(tags=["Notifications"])
class NotificationPreferenceView(RetrieveUpdateAPIView):
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        pref, _ = NotificationPreference.objects.get_or_create(user=self.request.user)
        return pref

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        return success_response(response.data)

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        return success_response(response.data)
