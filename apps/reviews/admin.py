from django.contrib import admin

from apps.reviews.models import Review, ReviewImage, StoreReview


class ReviewImageInline(admin.TabularInline):
    model = ReviewImage
    extra = 0


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["product", "user", "rating", "verified_purchase", "approved", "helpful_votes", "created_at"]
    list_filter = ["approved", "verified_purchase", "rating"]
    search_fields = ["product__name_en", "user__email", "title", "comment"]
    list_editable = ["approved"]
    inlines = [ReviewImageInline]
    actions = ["approve_reviews"]

    @admin.action(description="Approve selected reviews")
    def approve_reviews(self, request, queryset):
        queryset.update(approved=True)


@admin.register(StoreReview)
class StoreReviewAdmin(admin.ModelAdmin):
    list_display = ["customer_name", "rating", "product", "collected_on", "consent", "published", "created_at"]
    list_filter = ["published", "consent"]
    search_fields = ["customer_name", "comment"]
