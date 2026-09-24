"""
Public content endpoints. Everything here is readable by anonymous visitors —
this is the copy on the storefront — with a single throttled write surface:
the contact form.
"""
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import RetrieveAPIView
from rest_framework.views import APIView

from apps.content.models import (
    Branch,
    ContactPhone,
    FaqItem,
    Page,
    SiteSettings,
    SocialLink,
)
from apps.content.serializers import (
    ContactMessageCreateSerializer,
    FaqItemSerializer,
    PageListSerializer,
    PageSerializer,
    SiteBundleSerializer,
)
from apps.core.response import success_response


def client_ip(request):
    return (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
    )


@extend_schema(tags=["Content"], responses=SiteBundleSerializer)
class SiteBundleView(APIView):
    """
    GET /api/v1/content/site/

    Identity, phones, branches and social links in one payload — the header,
    the footer and every SEO block are built from it, so the storefront
    should fetch it once and cache it client-side.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        payload = {
            "settings": SiteSettings.load(),
            "phones": ContactPhone.objects.filter(is_active=True),
            "branches": Branch.objects.filter(is_active=True),
            "social": SocialLink.objects.filter(is_active=True),
        }
        return success_response(SiteBundleSerializer(payload, context={"request": request}).data)


@extend_schema(tags=["Content"], responses=PageListSerializer(many=True))
class PageListView(APIView):
    """Published pages, for the footer nav and sitemap. Deliberately unpaginated."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        pages = Page.objects.filter(is_published=True)
        return success_response(PageListSerializer(pages, many=True, context={"request": request}).data)


@extend_schema(tags=["Content"])
class PageDetailView(RetrieveAPIView):
    serializer_class = PageSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"

    def get_queryset(self):
        return Page.objects.filter(is_published=True).prefetch_related("sections")

    def retrieve(self, request, *args, **kwargs):
        return success_response(self.get_serializer(self.get_object()).data)


@extend_schema(tags=["Content"], responses=FaqItemSerializer(many=True))
class FaqListView(APIView):
    """
    Active FAQ items in display order. Unpaginated: the page renders all of
    them at once and emits FAQPage structured data from the same list.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        queryset = FaqItem.objects.filter(is_active=True)
        topic = request.query_params.get("topic")
        if topic:
            queryset = queryset.filter(topic=topic)
        return success_response(FaqItemSerializer(queryset, many=True).data)


@extend_schema(tags=["Content"], request=ContactMessageCreateSerializer, responses=None)
class ContactMessageCreateView(APIView):
    """
    POST /api/v1/content/contact-messages/

    Anonymous and throttled (`contact` scope). Nothing is echoed back beyond
    an acknowledgement, so the endpoint cannot be used to read other rows.
    """

    permission_classes = [permissions.AllowAny]
    throttle_scope = "contact"

    def post(self, request):
        serializer = ContactMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.save(ip_address=client_ip(request))
        return success_response(
            {"id": message.id, "created_at": message.created_at},
            message="Message received.",
            status=201,
        )
