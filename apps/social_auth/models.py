from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class SocialProvider(models.TextChoices):
    GOOGLE = "google", "Google"
    FACEBOOK = "facebook", "Facebook"


class SocialAccount(BaseModel):
    """
    One customer's identity at Google or Facebook, tied to a store account.

    The provider's own user id (`uid`) is what identifies the person on the next
    sign-in — not the email, which a Facebook user can change. The email is kept
    as it was when the link was made, for support and audit.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="social_accounts")
    provider = models.CharField(max_length=20, choices=SocialProvider.choices)
    uid = models.CharField(max_length=255)
    email = models.EmailField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "uid"], name="unique_social_identity")]
        indexes = [models.Index(fields=["user", "provider"])]

    def __str__(self):
        return f"{self.provider}:{self.uid} → {self.user_id}"
