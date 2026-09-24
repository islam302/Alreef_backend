from django.db import transaction

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.reviews.models import Review, ReviewHelpfulVote


def get_eligible_order_item(*, user, product):
    """Only customers who purchased & received the product may leave a
    verified review (spec §30)."""
    from apps.orders.models import OrderItem, OrderStatus

    return (
        OrderItem.objects.filter(
            order__user=user, order__status=OrderStatus.DELIVERED, product_id=product.id, review__isnull=True,
        )
        .order_by("-order__created_at")
        .first()
    )


@transaction.atomic
def create_review(*, user, product, rating: int, title: str = "", comment: str = ""):
    order_item = get_eligible_order_item(user=user, product=product)
    if not order_item:
        raise ApplicationError(
            ErrorCodes.REVIEW_NOT_ELIGIBLE,
            "Only customers with a delivered order for this product may leave a review.",
        )

    if Review.objects.filter(product=product, user=user, order_item=order_item).exists():
        raise ApplicationError(ErrorCodes.REVIEW_ALREADY_EXISTS, "You have already reviewed this purchase.")

    return Review.objects.create(
        product=product, user=user, order_item=order_item, rating=rating, title=title, comment=comment,
        verified_purchase=True,
    )


@transaction.atomic
def vote_helpful(*, review: Review, user):
    _, created = ReviewHelpfulVote.objects.get_or_create(review=review, user=user)
    if created:
        Review.objects.filter(pk=review.pk).update(helpful_votes=review.helpful_votes + 1)
    return created
