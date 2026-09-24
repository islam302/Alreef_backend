import re

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

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


# ---------------------------------------------------------------------------
# Public (storefront)
# ---------------------------------------------------------------------------
class ContactPhoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactPhone
        fields = ["id", "label", "display", "dial", "is_whatsapp", "is_primary", "sort_order"]


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = [
            "id", "name", "address", "region", "plus_code", "postal_code",
            "maps_url", "phone", "working_hours_ar", "sort_order",
        ]


class SocialLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = SocialLink
        fields = ["id", "platform", "label", "url", "sort_order"]


class SiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        fields = [
            "name_ar", "full_name_ar", "name_en", "legal_name_ar",
            "descriptor_ar", "descriptor_en", "tagline_ar", "tagline_en",
            "founded_year", "site_url", "logo", "og_image",
            "email", "support_hours_ar",
            "announcement_enabled", "announcement_text_ar", "announcement_url",
            "meta_title_ar", "meta_description_ar", "updated_at",
        ]


class SiteBundleSerializer(serializers.Serializer):
    """
    Everything the storefront chrome needs in one request: identity, phones,
    branches and social profiles. The header, footer and every SEO block read
    from this, so it is deliberately a single round trip.
    """

    settings = SiteSettingsSerializer()
    phones = ContactPhoneSerializer(many=True)
    branches = BranchSerializer(many=True)
    social = SocialLinkSerializer(many=True)


class PageSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageSection
        fields = [
            "id", "kind", "key", "eyebrow", "title", "body", "image", "image_alt",
            "icon", "link_url", "link_label", "data", "sort_order",
        ]


class PageListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Page
        fields = ["id", "slug", "title_ar", "title_en", "subtitle_ar", "sort_order", "updated_at"]


class PageSerializer(serializers.ModelSerializer):
    sections = serializers.SerializerMethodField()

    class Meta:
        model = Page
        fields = [
            "id", "slug", "title_ar", "title_en", "subtitle_ar", "body_ar", "body_en",
            "hero_image", "hero_image_alt", "meta_title", "meta_description",
            "sections", "updated_at",
        ]

    @extend_schema_field(PageSectionSerializer(many=True))
    def get_sections(self, obj):
        sections = [s for s in obj.sections.all() if s.is_active]
        return PageSectionSerializer(sections, many=True, context=self.context).data


class FaqItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FaqItem
        fields = ["id", "question", "answer", "topic", "sort_order"]


EG_MOBILE_RE = re.compile(r"^01[0125]\d{8}$")


class ContactMessageCreateSerializer(serializers.ModelSerializer):
    """
    Public write surface. Status, staff notes and the handler are set by staff
    only, so they are deliberately absent from the writable field list.
    """

    # Declared explicitly so the model's strict validator does not run before
    # the value is normalised: people type "010 6060 4405", and rejecting that
    # would be the form being pedantic rather than the number being wrong.
    phone = serializers.CharField(max_length=20)

    class Meta:
        model = ContactMessage
        fields = ["name", "phone", "email", "subject", "message"]

    def validate_name(self, value):
        value = value.strip()
        if len(value) < 3:
            raise serializers.ValidationError("Enter your name.")
        return value

    def validate_phone(self, value):
        cleaned = re.sub(r"[\s\-()]", "", value)
        if cleaned.startswith("+20"):
            cleaned = "0" + cleaned[3:]
        elif cleaned.startswith("20") and len(cleaned) == 12:
            cleaned = "0" + cleaned[2:]
        if not EG_MOBILE_RE.match(cleaned):
            raise serializers.ValidationError("Enter an Egyptian mobile number, e.g. 01060604405.")
        return cleaned

    def validate_message(self, value):
        value = value.strip()
        if len(value) < 10:
            raise serializers.ValidationError("Tell us a bit more so we can help.")
        return value


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
class AdminSiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        exclude = ["created_at"]
        read_only_fields = ["id", "updated_at"]


class AdminContactPhoneSerializer(serializers.ModelSerializer):
    """
    Exactly one number can be the primary one — the storefront reads it for
    the WhatsApp button and the "call us" strip — so promoting a number
    demotes the previous holder in the same transaction.
    """

    class Meta:
        model = ContactPhone
        fields = [
            "id", "label", "display", "dial", "is_whatsapp", "is_primary",
            "sort_order", "is_active", "updated_at",
        ]
        read_only_fields = ["updated_at"]

    @transaction.atomic
    def create(self, validated_data):
        instance = super().create(validated_data)
        self._demote_others(instance)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        self._demote_others(instance)
        return instance

    @staticmethod
    def _demote_others(instance):
        if instance.is_primary:
            ContactPhone.objects.exclude(pk=instance.pk).filter(is_primary=True).update(is_primary=False)


class AdminBranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = [
            "id", "name", "address", "region", "plus_code", "postal_code", "maps_url",
            "phone", "working_hours_ar", "sort_order", "is_active", "updated_at",
        ]
        read_only_fields = ["updated_at"]


class AdminSocialLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = SocialLink
        fields = ["id", "platform", "label", "url", "sort_order", "is_active", "updated_at"]
        read_only_fields = ["updated_at"]


class AdminPageSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageSection
        fields = [
            "id", "page", "kind", "key", "eyebrow", "title", "body", "image", "image_alt",
            "icon", "link_url", "link_label", "data", "sort_order", "is_active", "updated_at",
        ]
        read_only_fields = ["updated_at"]


class AdminPageListSerializer(serializers.ModelSerializer):
    section_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Page
        fields = [
            "id", "slug", "title_ar", "title_en", "is_published",
            "sort_order", "section_count", "updated_at",
        ]


class AdminPageSerializer(serializers.ModelSerializer):
    sections = AdminPageSectionSerializer(many=True, read_only=True)

    class Meta:
        model = Page
        fields = [
            "id", "slug", "title_ar", "title_en", "subtitle_ar", "body_ar", "body_en",
            "hero_image", "hero_image_alt", "meta_title", "meta_description",
            "is_published", "sort_order", "sections", "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class AdminFaqItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FaqItem
        fields = ["id", "question", "answer", "topic", "sort_order", "is_active", "updated_at"]
        read_only_fields = ["updated_at"]


class AdminContactMessageSerializer(serializers.ModelSerializer):
    handled_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ContactMessage
        fields = [
            "id", "name", "phone", "email", "subject", "message", "status", "staff_note",
            "handled_by", "handled_by_name", "handled_at", "ip_address", "created_at",
        ]
        # The message itself is customer-authored evidence: staff may triage it,
        # never rewrite it.
        read_only_fields = [
            "name", "phone", "email", "subject", "message",
            "handled_by", "handled_by_name", "handled_at", "ip_address", "created_at",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_handled_by_name(self, obj):
        return obj.handled_by.full_name if obj.handled_by else None

    def update(self, instance, validated_data):
        """Moving a message out of `new` stamps who dealt with it, and when."""
        status_changed = (
            "status" in validated_data and validated_data["status"] != instance.status
        )
        instance = super().update(instance, validated_data)
        if status_changed and instance.status != ContactMessageStatus.NEW:
            request = self.context.get("request")
            instance.handled_by = getattr(request, "user", None) if request else None
            instance.handled_at = timezone.now()
            instance.save(update_fields=["handled_by", "handled_at", "updated_at"])
        return instance


class ReorderSerializer(serializers.Serializer):
    """Payload for the bulk `reorder` actions: [{id, sort_order}, ...]."""

    class _Row(serializers.Serializer):
        id = serializers.IntegerField()
        sort_order = serializers.IntegerField(min_value=0, max_value=32767)

    items = _Row(many=True, allow_empty=False)
