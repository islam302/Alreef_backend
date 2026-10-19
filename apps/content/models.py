"""
Editable site content: the business facts (names, phones, branches, social
profiles), the static pages (about / shipping / guide ...), the FAQ, and the
messages the storefront contact form produces.

All of this used to be hardcoded in the storefront bundle, which meant a new
phone number needed a redeploy. These tables are the source of truth instead,
and the dashboard edits them through /api/v1/admin-api/.
"""
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from apps.core.models import BaseModel
from apps.core.validators import validate_image_file, validate_no_unsafe_html

# Egyptian mobile number in the form the storefront form accepts: 01[0125] + 8 digits.
EG_MOBILE_VALIDATOR = RegexValidator(
    regex=r"^01[0125]\d{8}$",
    message="Enter an Egyptian mobile number, e.g. 01012345678.",
)

# Dial-ready form used for tel:/wa.me links, e.g. 201001234567.
DIAL_VALIDATOR = RegexValidator(
    regex=r"^\d{8,15}$",
    message="Digits only, country code first, e.g. 201001234567.",
)


def site_asset_upload_path(instance, filename):
    return f"content/site/{filename}"


def page_image_upload_path(instance, filename):
    return f"content/pages/{instance.slug}/{filename}"


def section_image_upload_path(instance, filename):
    return f"content/pages/{instance.page.slug}/sections/{filename}"


class SiteSettings(BaseModel):
    """
    Singleton (pk=1). Always read it through `SiteSettings.load()` — the row
    is created on first access so a fresh database still answers the public
    endpoint instead of 404-ing the storefront header.
    """

    SINGLETON_ID = 1

    # --- Identity ---------------------------------------------------------
    name_ar = models.CharField(max_length=120, blank=True)
    full_name_ar = models.CharField(max_length=180, blank=True)
    name_en = models.CharField(max_length=120, blank=True)
    legal_name_ar = models.CharField(max_length=200, blank=True)
    descriptor_ar = models.CharField(max_length=180, blank=True)
    descriptor_en = models.CharField(max_length=180, blank=True)
    tagline_ar = models.CharField(max_length=200, blank=True)
    tagline_en = models.CharField(max_length=200, blank=True)
    founded_year = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1800)])
    site_url = models.URLField(blank=True)

    logo = models.ImageField(
        upload_to=site_asset_upload_path, null=True, blank=True, validators=[validate_image_file]
    )
    og_image = models.ImageField(
        upload_to=site_asset_upload_path, null=True, blank=True, validators=[validate_image_file],
        help_text="Default social-share image (1200x630 recommended).",
    )

    # --- Contact ----------------------------------------------------------
    email = models.EmailField(blank=True)
    support_hours_ar = models.CharField(max_length=200, blank=True)

    # --- Announcement bar -------------------------------------------------
    announcement_enabled = models.BooleanField(default=False)
    announcement_text_ar = models.CharField(max_length=240, blank=True, validators=[validate_no_unsafe_html])
    announcement_url = models.CharField(max_length=255, blank=True, help_text="Path or absolute URL. Optional.")

    # --- Default SEO ------------------------------------------------------
    meta_title_ar = models.CharField(max_length=180, blank=True)
    meta_description_ar = models.CharField(max_length=320, blank=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return self.name_ar or self.name_en or "Site settings"

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_ID
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # pragma: no cover - guarded, never used
        raise RuntimeError("SiteSettings is a singleton and cannot be deleted.")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_ID)
        return obj


class ContactPhone(BaseModel):
    """A number the shop actually answers. `dial` drives tel:/wa.me links."""

    label = models.CharField(max_length=80, blank=True)
    display = models.CharField(max_length=40, help_text="As printed, e.g. +20 10 01234567.")
    dial = models.CharField(max_length=20, validators=[DIAL_VALIDATOR], help_text="e.g. 201001234567")
    is_whatsapp = models.BooleanField(default=False)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["is_active", "sort_order"])]

    def __str__(self):
        return self.display


class Branch(BaseModel):
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255)
    region = models.CharField(max_length=120, blank=True, help_text="Governorate.")
    plus_code = models.CharField(max_length=32, blank=True)
    postal_code = models.CharField(max_length=16, blank=True)
    maps_url = models.URLField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    working_hours_ar = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name_plural = "Branches"

    def __str__(self):
        return self.name


class SocialPlatform(models.TextChoices):
    FACEBOOK = "facebook", "Facebook"
    INSTAGRAM = "instagram", "Instagram"
    TIKTOK = "tiktok", "TikTok"
    YOUTUBE = "youtube", "YouTube"
    WHATSAPP = "whatsapp", "WhatsApp"
    X = "x", "X"
    LINKEDIN = "linkedin", "LinkedIn"
    OTHER = "other", "Other"


class SocialLink(BaseModel):
    platform = models.CharField(max_length=20, choices=SocialPlatform.choices)
    label = models.CharField(max_length=80, blank=True, help_text="Arabic label shown in the UI.")
    url = models.URLField()
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.get_platform_display()} - {self.url}"


class Page(BaseModel):
    """
    A static storefront page (about, shipping, guide, contact intro ...).

    `body_ar` is plain text / light Markdown, never HTML: the storefront
    renders it as text, so a compromised staff account cannot inject script
    into a customer-facing page.
    """

    slug = models.SlugField(max_length=80, unique=True)
    title_ar = models.CharField(max_length=180)
    title_en = models.CharField(max_length=180, blank=True)
    subtitle_ar = models.CharField(max_length=320, blank=True)
    body_ar = models.TextField(blank=True, validators=[validate_no_unsafe_html])
    body_en = models.TextField(blank=True, validators=[validate_no_unsafe_html])
    hero_image = models.ImageField(
        upload_to=page_image_upload_path, null=True, blank=True, validators=[validate_image_file]
    )
    hero_image_alt = models.CharField(max_length=255, blank=True)
    meta_title = models.CharField(max_length=180, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)
    is_published = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["slug"]), models.Index(fields=["is_published"])]

    def __str__(self):
        return f"{self.title_ar} (/{self.slug})"


class SectionKind(models.TextChoices):
    TEXT = "text", "Text block"
    CARD = "card", "Card"
    STAT = "stat", "Statistic"
    IMAGE = "image", "Image"
    QUOTE = "quote", "Quote"
    CTA = "cta", "Call to action"


class PageSection(BaseModel):
    """
    Ordered blocks inside a page — the value cards on /about, the policy rows
    on /shipping. `data` carries block-specific extras the UI already knows
    about (counter target, list rows) without a migration per block type.
    """

    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="sections")
    kind = models.CharField(max_length=16, choices=SectionKind.choices, default=SectionKind.TEXT)
    key = models.SlugField(max_length=60, blank=True, help_text="Stable handle for the storefront. Optional.")
    eyebrow = models.CharField(max_length=120, blank=True)
    title = models.CharField(max_length=180, blank=True)
    body = models.TextField(blank=True, validators=[validate_no_unsafe_html])
    image = models.ImageField(
        upload_to=section_image_upload_path, null=True, blank=True, validators=[validate_image_file]
    )
    image_alt = models.CharField(max_length=255, blank=True)
    icon = models.CharField(max_length=40, blank=True, help_text="Icon name from the storefront icon set.")
    link_url = models.CharField(max_length=255, blank=True)
    link_label = models.CharField(max_length=120, blank=True)
    data = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["page", "sort_order"])]

    def __str__(self):
        return f"{self.page.slug}/{self.key or self.pk}"


class FaqItem(BaseModel):
    question = models.CharField(max_length=255)
    answer = models.TextField(validators=[validate_no_unsafe_html])
    topic = models.CharField(max_length=80, blank=True, help_text="Grouping label shown above the group.")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "FAQ item"

    def __str__(self):
        return self.question


class ContactMessageStatus(models.TextChoices):
    NEW = "new", "New"
    READ = "read", "Read"
    REPLIED = "replied", "Replied"
    ARCHIVED = "archived", "Archived"
    SPAM = "spam", "Spam"


class ContactMessage(BaseModel):
    """Submissions from the storefront contact form. Staff-visible only."""

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15, validators=[EG_MOBILE_VALIDATOR])
    email = models.EmailField(blank=True)
    subject = models.CharField(max_length=180, blank=True)
    message = models.TextField()

    status = models.CharField(max_length=12, choices=ContactMessageStatus.choices, default=ContactMessageStatus.NEW)
    staff_note = models.TextField(blank=True)
    handled_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="contact_messages"
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"{self.name} - {self.subject or self.message[:40]}"
