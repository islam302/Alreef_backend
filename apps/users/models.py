from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

from apps.core.models import BaseModel
from apps.core.validators import validate_image_file
from apps.users.managers import UserManager


def profile_image_upload_path(instance, filename):
    return f"users/{instance.pk}/profile/{filename}"


class User(AbstractBaseUser, PermissionsMixin):
    """Custom user model (spec §6). Email is the login identifier."""

    email = models.EmailField(unique=True)
    phone = PhoneNumberField(unique=True, null=True, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    profile_image = models.ImageField(upload_to=profile_image_upload_path, null=True, blank=True, validators=[validate_image_file])

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)

    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        indexes = [models.Index(fields=["email"]), models.Index(fields=["phone"])]

    def __str__(self):
        return self.email

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email


class Gender(models.TextChoices):
    MALE = "male", "Male"
    FEMALE = "female", "Female"
    UNSPECIFIED = "unspecified", "Prefer not to say"


class Language(models.TextChoices):
    ARABIC = "ar", "Arabic"
    ENGLISH = "en", "English"


class CustomerProfile(BaseModel):
    """Optional preference data (spec §7) — kept separate from auth-critical User fields."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=16, choices=Gender.choices, blank=True)
    marketing_consent = models.BooleanField(default=False)
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.ARABIC)
    currency = models.CharField(max_length=3, default="EGP")

    def __str__(self):
        return f"Profile<{self.user_id}>"


class AddressLabel(models.TextChoices):
    HOME = "home", "Home"
    WORK = "work", "Work"
    OTHER = "other", "Other"


class Address(BaseModel):
    """Egypt-focused address (spec §8). Shipping prices are NEVER stored here."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=16, choices=AddressLabel.choices, default=AddressLabel.HOME)
    full_name = models.CharField(max_length=150)
    phone = PhoneNumberField()

    governorate = models.ForeignKey("shipping.Governorate", on_delete=models.PROTECT, related_name="addresses")
    city = models.ForeignKey("shipping.City", on_delete=models.PROTECT, related_name="addresses", null=True, blank=True)
    district = models.CharField(max_length=150, blank=True)
    street = models.CharField(max_length=255)
    building = models.CharField(max_length=50, blank=True)
    floor = models.CharField(max_length=20, blank=True)
    apartment = models.CharField(max_length=20, blank=True)
    landmark = models.CharField(max_length=255, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    notes = models.CharField(max_length=500, blank=True)

    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "-created_at"]
        indexes = [models.Index(fields=["user", "is_default"])]

    def __str__(self):
        return f"{self.full_name} — {self.street}, {self.governorate}"


class EmailVerificationToken(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_verification_tokens")
    token = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)


class PhoneOTP(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="phone_otps")
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)


class PasswordResetToken(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
