from rest_framework import serializers

from apps.reviews.models import Review, ReviewImage


class ReviewImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewImage
        fields = ["id", "image"]


ANONYMOUS_REVIEWER = "عميل الريف"


def public_display_name(user) -> str:
    """How a reviewer is named in public: first name and last initial.

    `User.full_name` falls back to the email address, and a review is public —
    so a reviewer who never gave a name used to be published by email. Here
    the email never appears: no first name means a neutral label.
    """
    first = (user.first_name or "").strip()
    if not first:
        return ANONYMOUS_REVIEWER
    last = (user.last_name or "").strip()
    return f"{first} {last[0]}." if last else first


class ReviewSerializer(serializers.ModelSerializer):
    images = ReviewImageSerializer(many=True, read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = [
            "id", "user_name", "rating", "title", "comment", "images",
            "verified_purchase", "helpful_votes", "created_at",
        ]
        read_only_fields = fields

    def get_user_name(self, obj) -> str:
        return public_display_name(obj.user)


class ReviewProductSerializer(serializers.Serializer):
    name_ar = serializers.CharField()
    name_en = serializers.CharField()
    slug = serializers.CharField()


class LatestReviewSerializer(ReviewSerializer):
    """A review shown away from its product page (the homepage), so it
    names the product it is about."""

    product = ReviewProductSerializer(read_only=True)

    class Meta(ReviewSerializer.Meta):
        fields = ReviewSerializer.Meta.fields + ["product"]
        read_only_fields = fields


class ReviewCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True)
    comment = serializers.CharField(required=False, allow_blank=True)
