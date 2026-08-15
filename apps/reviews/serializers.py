from rest_framework import serializers

from apps.reviews.models import Review, ReviewImage


class ReviewImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewImage
        fields = ["id", "image"]


class ReviewSerializer(serializers.ModelSerializer):
    images = ReviewImageSerializer(many=True, read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Review
        fields = [
            "id", "user_name", "rating", "title", "comment", "images",
            "verified_purchase", "helpful_votes", "created_at",
        ]
        read_only_fields = fields


class ReviewCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    title = serializers.CharField(max_length=150, required=False, allow_blank=True)
    comment = serializers.CharField(required=False, allow_blank=True)
