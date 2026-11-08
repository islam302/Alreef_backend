from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.views import APIView

from apps.catalog.models import Product, ProductStatus
from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.core.response import success_response
from apps.core.schema import envelope
from apps.core.validators import validate_image_file
from apps.reviews import services
from apps.reviews.models import Review, ReviewImage, StoreReview
from apps.reviews.serializers import (
    GuestReviewCreateSerializer,
    LatestReviewSerializer,
    LatestStoreReviewSerializer,
    ReviewCreateSerializer,
    ReviewSerializer,
)

MAX_REVIEW_IMAGES = 5
LATEST_REVIEWS_DEFAULT = 8
LATEST_REVIEWS_MAX = 20


@extend_schema(tags=["Reviews"])
class ProductReviewListView(ListAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return Review.objects.filter(product_id=self.kwargs["product_id"], approved=True).select_related("user", "order_item__order").prefetch_related("images")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema(tags=["Reviews"], responses=envelope(LatestReviewSerializer(many=True), name="LatestReviews"))
class LatestReviewListView(APIView):
    """
    The newest public customer opinions, for the homepage — from both places
    customers buy:

    - `source: "online"` — approved reviews with written text, for products a
      customer can still buy.
    - `source: "in_store"` — opinions given at the branch, recorded by staff,
      published only with the customer's consent.

    Newest first (a branch opinion dates from the day it was given), never
    more than 20. A plain list, not paginated: it is a showcase, not an
    archive (the full set stays on each product's own reviews endpoint).
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", LATEST_REVIEWS_DEFAULT))
        except ValueError:
            limit = LATEST_REVIEWS_DEFAULT
        limit = max(1, min(limit, LATEST_REVIEWS_MAX))
        online = (
            Review.objects.filter(
                approved=True,
                product__published=True,
                product__status=ProductStatus.ACTIVE,
            )
            .exclude(comment="")
            .select_related("user", "product", "order_item__order")
            .prefetch_related("images")
            .order_by("-created_at")[:limit]
        )
        in_store = (
            StoreReview.objects.filter(published=True, consent=True)
            .exclude(comment="")
            .select_related("product")
            .order_by("-created_at")[:limit]
        )
        context = {"request": request}
        rows = [(r.created_at.date(), r.created_at, LatestReviewSerializer(r, context=context).data) for r in online]
        rows += [
            (r.collected_on or r.created_at.date(), r.created_at, LatestStoreReviewSerializer(r, context=context).data)
            for r in in_store
        ]
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return success_response([data for _, _, data in rows[:limit]])


@extend_schema(tags=["Reviews"], request=ReviewCreateSerializer, responses=envelope(ReviewSerializer, name="ReviewCreate"))
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
            # validate_image_file raises Django's ValidationError, which the API
            # error handler does not know — a rejected upload came back as a 500.
            try:
                validate_image_file(image)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"images": exc.messages}) from exc

        review = services.create_review(
            user=request.user, product=product, rating=serializer.validated_data["rating"],
            title=serializer.validated_data.get("title", ""), comment=serializer.validated_data.get("comment", ""),
        )
        ReviewImage.objects.bulk_create([ReviewImage(review=review, image=image) for image in images])
        return success_response(ReviewSerializer(review).data, message="Review submitted for moderation.", status=201)


@extend_schema(tags=["Reviews"], request=GuestReviewCreateSerializer, responses=envelope(ReviewSerializer, name="GuestReviewCreate"))
class GuestReviewCreateView(APIView):
    """
    POST /api/v1/reviews/guest/ — a review from somebody who ordered without an
    account, for one item of their delivered order.

    The order is proved exactly as on the tracking page (apps/orders/guest.py):
    the access token, or the order number with its phone. Every miss is the
    same 404, wrong phones count towards the same per-order lock, and the
    tight lookup throttle applies, so this cannot be used to probe orders.
    """

    permission_classes = [permissions.AllowAny]
    throttle_scope = "order_lookup"

    def post(self, request):
        from apps.orders import guest

        serializer = GuestReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data["token"]:
            order = guest.order_for_token(data["token"])
        else:
            try:
                order = guest.order_for_number_and_phone(data["order_number"], data["phone"])
            except guest.LookupLocked:
                raise ApplicationError(
                    ErrorCodes.THROTTLED, "Too many attempts for this order number. Please try again later.",
                )
        if order is None:
            raise ApplicationError(ErrorCodes.ORDER_NOT_FOUND, "We could not find an order with these details.")

        review = services.create_guest_review(
            order=order, order_item_id=data["order_item_id"], rating=data["rating"],
            title=data["title"].strip(), comment=data["comment"].strip(),
        )
        return success_response(ReviewSerializer(review).data, message="Review submitted for moderation.", status=201)


@extend_schema(
    # POST with no body: the action is the URL.
    request=None,
    tags=["Reviews"], responses=envelope(ReviewSerializer, name="ReviewVoteHelpful"),
)
class ReviewVoteHelpfulView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, review_id):
        review = Review.objects.filter(pk=review_id, approved=True).first()
        if not review:
            raise ApplicationError(ErrorCodes.NOT_FOUND, "Review not found.")
        services.vote_helpful(review=review, user=request.user)
        review.refresh_from_db()
        return success_response(ReviewSerializer(review).data)
