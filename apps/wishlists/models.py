from django.db import models

from apps.core.models import BaseModel


class WishlistItem(BaseModel):
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="wishlist_items")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="wishlisted_by")

    class Meta:
        unique_together = [("user", "product")]
        indexes = [models.Index(fields=["user"])]

    def __str__(self):
        return f"{self.user_id} -> {self.product_id}"
