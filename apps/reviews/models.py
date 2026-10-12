from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import BaseModel
from apps.core.validators import validate_image_file


class Review(BaseModel):
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="reviews")
    order_item = models.ForeignKey(
        "orders.OrderItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="review",
        help_text="Set when the review is tied to a verified purchase.",
    )

    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(max_length=150, blank=True)
    comment = models.TextField(blank=True)

    verified_purchase = models.BooleanField(default=False)
    approved = models.BooleanField(default=False, help_text="Moderated before appearing publicly")
    helpful_votes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("product", "user", "order_item")]
        indexes = [models.Index(fields=["product", "approved"])]

    def __str__(self):
        return f"{self.rating}★ {self.product} by {self.user_id}"


def review_image_upload_path(instance, filename):
    return f"reviews/{instance.review_id}/{filename}"


class ReviewImage(BaseModel):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to=review_image_upload_path, validators=[validate_image_file])

    class Meta:
        ordering = ["created_at"]


class StoreReview(BaseModel):
    """
    An opinion a customer gave at the branch, written down by staff.

    Kept apart from `Review` on purpose: a `Review` is typed by a signed-in
    customer about a product they can buy online, and only moderation touches
    it. A branch customer has no account and may not name a product, so their
    words are recorded here — with the name they agreed to be shown under, a
    rating only if they gave one, and their consent to publish.
    """

    customer_name = models.CharField(
        max_length=60,
        help_text="As the customer agreed to be named publicly, e.g. 'محمد أ.'. Never a phone or a full name.",
    )
    comment = models.TextField(help_text="The customer's words, as they said them.")
    rating = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Only if the customer gave one.",
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="store_reviews",
        help_text="What they bought, if known.",
    )
    collected_on = models.DateField(null=True, blank=True, help_text="The day the customer gave it.")
    consent = models.BooleanField(default=False, help_text="The customer agreed to have this published.")
    published = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["published", "consent"])]

    def __str__(self):
        return f"{self.customer_name} (branch)"


class ReviewHelpfulVote(BaseModel):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="review_votes")

    class Meta:
        unique_together = [("review", "user")]
