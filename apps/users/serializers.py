from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.users import services
from apps.users.models import Address, CustomerProfile, User


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "first_name", "last_name", "full_name",
            "profile_image", "email_verified", "phone_verified", "date_joined", "last_login",
        ]
        read_only_fields = ["id", "email", "email_verified", "phone_verified", "date_joined", "last_login"]


class CustomerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = ["date_of_birth", "gender", "marketing_consent", "language", "currency"]


class MeSerializer(serializers.ModelSerializer):
    profile = CustomerProfileSerializer(required=False)
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "first_name", "last_name", "full_name",
            "profile_image", "email_verified", "phone_verified", "date_joined", "last_login", "profile",
        ]
        read_only_fields = ["id", "email", "email_verified", "phone_verified", "date_joined", "last_login"]

    def update(self, instance, validated_data):
        profile_data = validated_data.pop("profile", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if profile_data:
            profile, _ = CustomerProfile.objects.get_or_create(user=instance)
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
            profile.save()
        return instance


class RegisterSerializer(serializers.ModelSerializer):
    # Declared explicitly (without ModelSerializer's automatic UniqueValidator)
    # so our custom validate_email/validate_phone raise the intended
    # AUTH_EMAIL_ALREADY_EXISTS / AUTH_PHONE_ALREADY_EXISTS error codes
    # instead of a generic UniqueValidator 400 pre-empting them.
    email = serializers.EmailField()
    phone = PhoneNumberField(required=False, allow_null=True)
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["email", "phone", "first_name", "last_name", "password"]

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise ApplicationError(ErrorCodes.AUTH_EMAIL_ALREADY_EXISTS, "An account with this email already exists.")
        return value

    def validate_phone(self, value):
        if value and User.objects.filter(phone=value).exists():
            raise ApplicationError(ErrorCodes.AUTH_PHONE_ALREADY_EXISTS, "An account with this phone number already exists.")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class LoginSerializer(TokenObtainPairSerializer):
    username_field = User.USERNAME_FIELD

    def validate(self, attrs):
        email = attrs.get("email")
        password = attrs.get("password")

        if email and services.is_login_locked(email):
            raise ApplicationError(
                ErrorCodes.AUTH_ACCOUNT_LOCKED, "Too many failed login attempts. Try again in a few minutes."
            )

        user = authenticate(request=self.context.get("request"), email=email, password=password)
        if not user:
            if email:
                services.record_failed_login(email)
            raise ApplicationError(ErrorCodes.AUTH_INVALID_CREDENTIALS, "Invalid email or password.")
        if not user.is_active:
            raise ApplicationError(ErrorCodes.AUTH_ACCOUNT_DISABLED, "This account has been disabled.")

        services.reset_login_attempts(email)
        data = super().validate(attrs)
        data["user"] = UserSerializer(user).data
        return data


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise ApplicationError(ErrorCodes.AUTH_INVALID_CREDENTIALS, "Current password is incorrect.")
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])


class EmailVerifyRequestSerializer(serializers.Serializer):
    """No body required — resends to request.user.email."""


class EmailVerifyConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()


class PhoneVerifyRequestSerializer(serializers.Serializer):
    phone = serializers.CharField(required=False)


class PhoneVerifyConfirmSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6, min_length=4)


class AddressSerializer(serializers.ModelSerializer):
    governorate_name = serializers.CharField(source="governorate.name_en", read_only=True)
    city_name = serializers.CharField(source="city.name_en", read_only=True, default=None)

    class Meta:
        model = Address
        fields = [
            "id", "label", "full_name", "phone", "governorate", "governorate_name", "city", "city_name",
            "district", "street", "building", "floor", "apartment", "landmark", "postal_code", "notes",
            "is_default", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)
