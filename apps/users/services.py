import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from apps.core.error_codes import ApplicationError, ErrorCodes
from apps.users.models import EmailVerificationToken, PasswordResetToken, PhoneOTP

EMAIL_TOKEN_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)
OTP_TTL = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5

# Per-account brute-force lockout (spec §101 auth hardening). Throttling in
# config/settings/base.py is IP/token-bucket based and easily distributed;
# this is a second, account-keyed layer that survives a rotating-IP attacker.
LOGIN_LOCKOUT_THRESHOLD = 10
LOGIN_LOCKOUT_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_DURATION_SECONDS = 15 * 60


def _login_attempts_cache_key(email: str) -> str:
    return f"login_attempts:{email.lower()}"


def _login_lock_cache_key(email: str) -> str:
    return f"login_locked:{email.lower()}"


def is_login_locked(email: str) -> bool:
    return cache.get(_login_lock_cache_key(email)) is not None


def record_failed_login(email: str) -> None:
    key = _login_attempts_cache_key(email)
    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=LOGIN_LOCKOUT_WINDOW_SECONDS)
        attempts = 1
    if attempts >= LOGIN_LOCKOUT_THRESHOLD:
        cache.set(_login_lock_cache_key(email), True, timeout=LOGIN_LOCKOUT_DURATION_SECONDS)


def reset_login_attempts(email: str) -> None:
    cache.delete(_login_attempts_cache_key(email))
    cache.delete(_login_lock_cache_key(email))


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_email_verification_token(user) -> str:
    token = _generate_token()
    EmailVerificationToken.objects.create(user=user, token=token, expires_at=timezone.now() + EMAIL_TOKEN_TTL)
    return token


def confirm_email_verification_token(token: str):
    record = EmailVerificationToken.objects.filter(token=token, used_at__isnull=True).select_related("user").first()
    if not record or record.expires_at < timezone.now():
        raise ApplicationError(ErrorCodes.AUTH_OTP_EXPIRED, "Verification link is invalid or has expired.")
    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])
    user = record.user
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    return user


def issue_password_reset_token(user) -> str:
    token = _generate_token()
    PasswordResetToken.objects.create(user=user, token=token, expires_at=timezone.now() + PASSWORD_RESET_TTL)
    return token


def confirm_password_reset(token: str, new_password: str):
    record = PasswordResetToken.objects.filter(token=token, used_at__isnull=True).select_related("user").first()
    if not record or record.expires_at < timezone.now():
        raise ApplicationError(ErrorCodes.AUTH_OTP_EXPIRED, "Reset link is invalid or has expired.")
    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])
    user = record.user
    user.set_password(new_password)
    user.save(update_fields=["password"])
    return user


def issue_phone_otp(user) -> str:
    code = _generate_otp_code()
    PhoneOTP.objects.create(user=user, code_hash=make_password(code), expires_at=timezone.now() + OTP_TTL)
    return code


def confirm_phone_otp(user, code: str):
    record = PhoneOTP.objects.filter(user=user, used_at__isnull=True).order_by("-created_at").first()
    if not record or record.expires_at < timezone.now():
        raise ApplicationError(ErrorCodes.AUTH_OTP_EXPIRED, "OTP has expired. Request a new one.")
    if record.attempts >= OTP_MAX_ATTEMPTS:
        raise ApplicationError(ErrorCodes.AUTH_OTP_INVALID, "Too many attempts. Request a new OTP.")
    if not check_password(code, record.code_hash):
        record.attempts += 1
        record.save(update_fields=["attempts"])
        raise ApplicationError(ErrorCodes.AUTH_OTP_INVALID, "Incorrect verification code.")
    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])
    user.phone_verified = True
    user.save(update_fields=["phone_verified"])
    return user
