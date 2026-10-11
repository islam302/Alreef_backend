from django.urls import path

from apps.reviews.views import (
    LatestReviewListView,
    ProductReviewListView,
    ReviewCreateView,
    ReviewVoteHelpfulView,
)

app_name = "reviews"

urlpatterns = [
    path("products/<int:product_id>/reviews/", ProductReviewListView.as_view(), name="list"),
    path("reviews/", ReviewCreateView.as_view(), name="create"),
    path("reviews/latest/", LatestReviewListView.as_view(), name="latest"),
    path("reviews/<int:review_id>/helpful/", ReviewVoteHelpfulView.as_view(), name="helpful"),
]
