"""
Staff content management, mounted under /api/v1/admin-api/.

Every viewset extends `EnvelopeViewSet`, so they inherit the same three
guarantees as the rest of the staff API: `IsAdminUser`, the standard response
envelope, and an AuditLog row per mutation.
"""
from django.db.models import Count
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.views import APIView

from apps.admin_api.mixins import EnvelopeViewSet
from apps.audit.models import AuditAction
from apps.audit.services import record
from apps.content.models import (
    Branch,
    ContactMessage,
    ContactMessageStatus,
    ContactPhone,
    FaqItem,
    Page,
    PageSection,
    SiteSettings,
    SocialLink,
)
from apps.content.serializers import (
    AdminBranchSerializer,
    AdminContactMessageSerializer,
    AdminContactPhoneSerializer,
    AdminFaqItemSerializer,
    AdminPageListSerializer,
    AdminPageSectionSerializer,
    AdminPageSerializer,
    AdminSiteSettingsSerializer,
    AdminSocialLinkSerializer,
    ReorderSerializer,
)
from apps.core.response import success_response

JSON_AND_FILE_PARSERS = [MultiPartParser, FormParser, *APIView.parser_classes]


class ReorderableMixin:
    """
    POST {"items": [{"id": 3, "sort_order": 0}, ...]} to re-sequence a list in
    one request — drag-and-drop in the dashboard should not fire N patches.
    """

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rows = {row["id"]: row["sort_order"] for row in serializer.validated_data["items"]}

        model = self.get_queryset().model
        objects = list(self.get_queryset().filter(pk__in=rows))
        for obj in objects:
            obj.sort_order = rows[obj.pk]
        if objects:
            model.objects.bulk_update(objects, ["sort_order"])

        record(
            actor=request.user, action=AuditAction.UPDATE,
            model_name=self.audit_model_name or self.get_queryset().model.__name__,
            object_id="", changes={"reordered": rows}, request=request,
        )
        return success_response(
            self.get_serializer(self.filter_queryset(self.get_queryset()), many=True).data,
            message="Order updated.",
        )


@extend_schema_view(
    get=extend_schema(tags=["Admin"], responses=AdminSiteSettingsSerializer),
    patch=extend_schema(tags=["Admin"], request=AdminSiteSettingsSerializer, responses=AdminSiteSettingsSerializer),
)
class AdminSiteSettingsView(APIView):
    """
    GET/PATCH /api/v1/admin-api/site-settings/

    A singleton, so it is a plain view rather than a viewset: there is
    nothing to list and nothing to create or delete.
    """

    permission_classes = [IsAdminUser]
    parser_classes = JSON_AND_FILE_PARSERS

    def get(self, request):
        return success_response(AdminSiteSettingsSerializer(SiteSettings.load(), context={"request": request}).data)

    def patch(self, request):
        settings_row = SiteSettings.load()
        serializer = AdminSiteSettingsSerializer(
            settings_row, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        record(
            actor=request.user, action=AuditAction.UPDATE, model_name="SiteSettings",
            object_id=settings_row.pk, changes={"fields": sorted(request.data.keys())}, request=request,
        )
        return success_response(serializer.data, message="Site settings updated.")


@extend_schema(tags=["Admin"])
class AdminContactPhoneViewSet(ReorderableMixin, EnvelopeViewSet):
    serializer_class = AdminContactPhoneSerializer
    audit_model_name = "ContactPhone"
    queryset = ContactPhone.objects.all()
    pagination_class = None


@extend_schema(tags=["Admin"])
class AdminBranchViewSet(ReorderableMixin, EnvelopeViewSet):
    serializer_class = AdminBranchSerializer
    audit_model_name = "Branch"
    queryset = Branch.objects.all()
    pagination_class = None


@extend_schema(tags=["Admin"])
class AdminSocialLinkViewSet(ReorderableMixin, EnvelopeViewSet):
    serializer_class = AdminSocialLinkSerializer
    audit_model_name = "SocialLink"
    queryset = SocialLink.objects.all()
    pagination_class = None


@extend_schema(tags=["Admin"])
class AdminFaqItemViewSet(ReorderableMixin, EnvelopeViewSet):
    serializer_class = AdminFaqItemSerializer
    audit_model_name = "FaqItem"
    queryset = FaqItem.objects.all()
    pagination_class = None
    search_fields = ["question", "answer", "topic"]


@extend_schema(tags=["Admin"])
class AdminPageViewSet(ReorderableMixin, EnvelopeViewSet):
    """Pages, with their sections nested on detail reads."""

    serializer_class = AdminPageSerializer
    list_serializer_class = AdminPageListSerializer
    audit_model_name = "Page"
    pagination_class = None
    parser_classes = JSON_AND_FILE_PARSERS
    lookup_value_regex = r"[^/]+"
    search_fields = ["slug", "title_ar", "title_en"]

    def get_queryset(self):
        queryset = Page.objects.all()
        if self.action == "list":
            # Aggregation drops the model's Meta.ordering, so it is restated
            # here — the dashboard list must match the storefront's order.
            return queryset.annotate(section_count=Count("sections")).order_by("sort_order", "id")
        return queryset.prefetch_related("sections")

    def get_object(self):
        """Accept either the numeric id or the slug, so dashboard deep links work."""
        lookup = self.kwargs[self.lookup_field]
        queryset = self.filter_queryset(self.get_queryset())
        page = (
            queryset.filter(pk=lookup).first()
            if str(lookup).isdigit()
            else queryset.filter(slug=lookup).first()
        )
        if page is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("Page not found.")
        self.check_object_permissions(self.request, page)
        return page

    @action(detail=True, methods=["post"], url_path="sections", parser_classes=JSON_AND_FILE_PARSERS)
    def add_section(self, request, pk=None):
        page = self.get_object()
        # request.data is a QueryDict on multipart uploads; copy it either way
        # so `page` can be injected without mutating the request.
        payload = request.data.dict() if hasattr(request.data, "dict") else dict(request.data)
        payload["page"] = page.pk
        serializer = AdminPageSectionSerializer(data=payload, context={"request": request})
        serializer.is_valid(raise_exception=True)
        section = serializer.save(page=page)
        self._audit(AuditAction.CREATE, page, {"section_added": section.pk})
        return success_response(
            AdminPageSectionSerializer(section, context={"request": request}).data,
            message="Section created.", status=201,
        )


@extend_schema(tags=["Admin"])
class AdminPageSectionViewSet(ReorderableMixin, EnvelopeViewSet):
    serializer_class = AdminPageSectionSerializer
    audit_model_name = "PageSection"
    pagination_class = None
    parser_classes = JSON_AND_FILE_PARSERS

    def get_queryset(self):
        queryset = PageSection.objects.select_related("page")
        page = self.request.query_params.get("page")
        if page:
            queryset = queryset.filter(page_id=page) if str(page).isdigit() else queryset.filter(page__slug=page)
        return queryset


@extend_schema(tags=["Admin"])
class AdminContactMessageViewSet(EnvelopeViewSet):
    """
    The contact-form inbox. Customer-authored fields are read-only; staff may
    change `status` and `staff_note` only.
    """

    http_method_names = ["get", "patch", "delete", "head", "options"]
    serializer_class = AdminContactMessageSerializer
    audit_model_name = "ContactMessage"
    search_fields = ["name", "phone", "subject", "message"]

    def get_queryset(self):
        queryset = ContactMessage.objects.select_related("handled_by")
        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status__in=[s for s in status.split(",") if s])
        return queryset

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = ContactMessage.objects.filter(status=ContactMessageStatus.NEW).count()
        return success_response({"count": count})
