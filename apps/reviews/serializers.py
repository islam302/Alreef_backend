from rest_framework import serializers

from apps.catalog.models import ProductStatus
from apps.reviews.models import Review, ReviewImage, StoreReview


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


def guest_display_name(full_name: str) -> str:
    """The same "first name, last initial" for a guest, from the name typed at
    checkout. Anything that looks like a phone or an email is never shown."""
    words = (full_name or "").split()
    if not words or any("@" in w or any(ch.isdigit() for ch in w) for w in words):
        return ANONYMOUS_REVIEWER
    return f"{words[0]} {words[-1][0]}." if len(words) > 1 else words[0]


def reviewer_name(review) -> str:
    if review.user_id:
        return public_display_name(review.user)
    order_item = review.order_item
    return guest_display_name(order_item.order.customer_name if order_item else "")


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
        return reviewer_name(obj)


class ReviewProductSerializer(serializers.Serializer):
    name_ar = serializers.CharField()
    name_en = serializers.CharField()
    slug = serializers.CharField()


class ReviewSource:
    ONLINE = "online"
    IN_STORE = "in_store"


class LatestReviewSerializer(ReviewSerializer):
    """A review shown away from its product page (the homepage), so it
    names the product it is about, and where it came from."""

    product = ReviewProductSerializer(read_only=True)
    source = serializers.SerializerMethodField()

    class Meta(ReviewSerializer.Meta):
        fields = ReviewSerializer.Meta.fields + ["product", "source"]
        read_only_fields = fields

    def get_source(self, obj) -> str:
        return ReviewSource.ONLINE


class LatestStoreReviewSerializer(serializers.ModelSerializer):
    """A branch customer's opinion in the same shape as an online review, so
    the homepage renders one list. What a branch review cannot know stays
    honest: no title, no photos, never a verified purchase, a rating only if
    the customer gave one, and a product only while it is still on sale."""

    user_name = serializers.CharField(source="customer_name")
    title = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    verified_purchase = serializers.SerializerMethodField()
    helpful_votes = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()

    class Meta:
        model = StoreReview
        fields = [
            "id", "user_name", "rating", "title", "comment", "images", "verified_purchase",
            "helpful_votes", "created_at", "collected_on", "product", "source",
        ]
        read_only_fields = fields

    def get_title(self, obj) -> str:
        return ""

    def get_images(self, obj) -> list:
        return []

    def get_verified_purchase(self, obj) -> bool:
        return False

    def get_helpful_votes(self, obj) -> int:
        return 0

    def get_source(self, obj) -> str:
        return ReviewSource.IN_STORE

    def get_product(self, obj) -> dict | None:
        product = obj.product
        if not product or not product.published or product.status != ProductStatus.ACTIVE:
            return None
        return ReviewProductSerializer(product).data


class ReviewCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True)
    comment = serializers.CharField(required=False, allow_blank=True)


class GuestReviewCreateSerializer(serializers.Serializer):
    """A guest's review of one line of their delivered order. The order is
    proved the way the tracking page proves it: the access token checkout
    returned, or the order number with the phone it was placed with."""

    token = serializers.CharField(required=False, allow_blank=True, max_length=512, default="")
    order_number = serializers.CharField(required=False, allow_blank=True, max_length=30, default="")
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32, default="")
    order_item_id = serializers.IntegerField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not attrs["token"] and not (attrs["order_number"] and attrs["phone"]):
            raise serializers.ValidationError("Send the order's token, or its number and phone.")
        return attrs
