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
def create_guest_review(*, order, order_item_id: int, rating: int, title: str = "", comment: str = ""):
    """A review from a guest, for one line of their own delivered order.

    The caller has already proved the order is theirs (access token, or order
    number + phone). Same rules as an account: delivered orders only, one
    review per purchased line, moderated before it shows."""
    from apps.orders.models import OrderItem, OrderStatus

    if order.status != OrderStatus.DELIVERED:
        raise ApplicationError(
            ErrorCodes.REVIEW_NOT_ELIGIBLE,
            "Only customers with a delivered order for this product may leave a review.",
        )
    order_item = (
        OrderItem.objects.select_for_update().select_related("product")
        .filter(pk=order_item_id, order=order, product__isnull=False).first()
    )
    if order_item is None:
        raise ApplicationError(ErrorCodes.REVIEW_NOT_ELIGIBLE, "This product is not on this order.")
    if Review.objects.filter(order_item=order_item).exists():
        raise ApplicationError(ErrorCodes.REVIEW_ALREADY_EXISTS, "You have already reviewed this purchase.")

    return Review.objects.create(
        product=order_item.product, user=None, order_item=order_item, rating=rating, title=title,
        comment=comment, verified_purchase=True,
    )


@transaction.atomic
def vote_helpful(*, review: Review, user):
    _, created = ReviewHelpfulVote.objects.get_or_create(review=review, user=user)
    if created:
        Review.objects.filter(pk=review.pk).update(helpful_votes=review.helpful_votes + 1)
    return created
