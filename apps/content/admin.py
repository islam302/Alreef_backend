from django.contrib import admin

from apps.content.models import (
    Branch,
    ContactMessage,
    ContactPhone,
    FaqItem,
    Page,
    PageSection,
    SiteSettings,
    SocialLink,
)


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ["__str__", "founded_year", "announcement_enabled", "updated_at"]

    def has_add_permission(self, request):
        # Singleton: the row is created by SiteSettings.load().
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ContactPhone)
class ContactPhoneAdmin(admin.ModelAdmin):
    list_display = ["display", "dial", "is_whatsapp", "is_primary", "is_active", "sort_order"]
    list_filter = ["is_active", "is_whatsapp", "is_primary"]
    list_editable = ["sort_order", "is_active"]


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ["name", "region", "address", "is_active", "sort_order"]
    list_filter = ["is_active", "region"]
    search_fields = ["name", "address", "region"]


@admin.register(SocialLink)
class SocialLinkAdmin(admin.ModelAdmin):
    list_display = ["platform", "label", "url", "is_active", "sort_order"]
    list_filter = ["is_active", "platform"]


class PageSectionInline(admin.StackedInline):
    model = PageSection
    extra = 0
    fields = [
        "kind", "key", "eyebrow", "title", "body", "image", "image_alt",
        "icon", "link_url", "link_label", "data", "sort_order", "is_active",
    ]


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ["slug", "title_ar", "is_published", "sort_order", "updated_at"]
    list_filter = ["is_published"]
    search_fields = ["slug", "title_ar", "title_en"]
    prepopulated_fields = {"slug": ("title_en",)}
    inlines = [PageSectionInline]


@admin.register(FaqItem)
class FaqItemAdmin(admin.ModelAdmin):
    list_display = ["question", "topic", "is_active", "sort_order"]
    list_filter = ["is_active", "topic"]
    search_fields = ["question", "answer"]


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "subject", "status", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["name", "phone", "subject", "message"]
    readonly_fields = ["name", "phone", "email", "subject", "message", "ip_address", "created_at"]
