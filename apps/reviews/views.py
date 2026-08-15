from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.views import APIView

from apps.catalog.models import Product
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.core.validators import validate_image_file
from apps.reviews import services
from apps.reviews.models import Review, ReviewImage
from apps.reviews.serializers import ReviewCreateSerializer, ReviewSerializer

MAX_REVIEW_IMAGES = 5


@extend_schema(tags=["Reviews"])
class ProductReviewListView(ListAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return Review.objects.filter(product_id=self.kwargs["product_id"], approved=True).select_related("user").prefetch_related("images")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema(tags=["Reviews"], request=ReviewCreateSerializer)
class ReviewCreateView(APIView):
    """
    Accepts either application/json or multipart/form-data. To attach
    photos, POST multipart/form-data with the same fields as
    ReviewCreateSerializer plus up to 5 `images` file parts (spec §84).
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, *APIView.parser_classes]
    throttle_scope = "review"

    def post(self, request):
        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = Product.objects.filter(pk=serializer.validated_data["product_id"]).first()
        if not product:
            raise ApplicationError(ErrorCodes.PRODUCT_NOT_FOUND, "Product not found.")

        images = request.FILES.getlist("images")[:MAX_REVIEW_IMAGES]
        for image in images:
            validate_image_file(image)

        review = services.create_review(
            user=request.user, product=product, rating=serializer.validated_data["rating"],
            title=serializer.validated_data.get("title", ""), comment=serializer.validated_data.get("comment", ""),
        )
        ReviewImage.objects.bulk_create([ReviewImage(review=review, image=image) for image in images])
        return success_response(ReviewSerializer(review).data, message="Review submitted for moderation.", status=201)


@extend_schema(tags=["Reviews"])
class ReviewVoteHelpfulView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, review_id):
        review = Review.objects.filter(pk=review_id, approved=True).first()
        if not review:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Review not found.")
        services.vote_helpful(review=review, user=request.user)
        review.refresh_from_db()
        return success_response(ReviewSerializer(review).data)
