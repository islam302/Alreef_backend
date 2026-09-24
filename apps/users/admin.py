from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.users.models import Address, CustomerProfile, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["-date_joined"]
    list_display = ["email", "phone", "first_name", "last_name", "is_staff", "is_active", "email_verified", "date_joined"]
    list_filter = ["is_staff", "is_active", "email_verified", "phone_verified"]
    search_fields = ["email", "phone", "first_name", "last_name"]
    readonly_fields = ["date_joined", "last_login"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone", "profile_image")}),
        ("Verification", {"fields": ("email_verified", "phone_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "is_staff", "is_active")}),
    )


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "language", "currency", "marketing_consent"]
    autocomplete_fields = ["user"]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ["user", "label", "governorate", "city", "is_default"]
    list_filter = ["governorate", "label", "is_default"]
    search_fields = ["user__email", "full_name", "street"]
    autocomplete_fields = ["user"]
