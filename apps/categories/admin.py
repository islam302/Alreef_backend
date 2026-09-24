from django.contrib import admin
from mptt.admin import DraggableMPTTAdmin

from apps.categories.models import Category


@admin.register(Category)
class CategoryAdmin(DraggableMPTTAdmin):
    list_display = ["tree_actions", "indented_title", "slug", "is_active", "sort_order"]
    list_display_links = ["indented_title"]
    list_filter = ["is_active"]
    search_fields = ["name_en", "name_ar", "slug"]
    prepopulated_fields = {"slug": ("name_en",)}
