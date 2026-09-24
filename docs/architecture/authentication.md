# Authentication

All auth endpoints live under `/api/v1/auth/` (`apps/users/urls_auth.py`),
implemented in `apps/users/views.py`, backed by `apps/users/services.py` and
`apps/users/models.py`. Login is throttled with the `auth` scope (`20/min`,
`REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` in `config/settings/base.py`).

## Custom User model

`apps.users.models.User` (`AUTH_USER_MODEL = "users.User"`) extends
`AbstractBaseUser` + `PermissionsMixin` directly rather than Django's default
`AbstractUser`:

- `USERNAME_FIELD = "email"`, `REQUIRED_FIELDS = []` — **email is the login
  identifier**, there is no separate username.
- `phone` is a unique, nullable `PhoneNumberField` (django-phonenumber-field)
  — a user can register with just an email and add/verify a phone later.
- `email_verified` / `phone_verified` are independent booleans; nothing in
  the codebase currently blocks login on `email_verified=False`, but
  `ErrorCodes.AUTH_EMAIL_NOT_VERIFIED` exists in `apps/core/error_codes.py`
  for endpoints that choose to enforce it.
- `apps.users.managers.UserManager.create_superuser` forces
  `email_verified=True` so `createsuperuser` never gets locked out by an
  email-verification gate.

`CustomerProfile` (date of birth, gender, marketing consent, language,
currency) is a separate one-to-one model — kept apart from the
auth-critical `User` fields on purpose (`apps/users/models.py` docstring).

## JWT flow (SimpleJWT)

Configured in `config/settings/base.py` under `SIMPLE_JWT`:

| Setting | Value |
|---|---|
| `ACCESS_TOKEN_LIFETIME` | 15 minutes (`JWT_ACCESS_MINUTES` env, default 15) |
| `REFRESH_TOKEN_LIFETIME` | 14 days (`JWT_REFRESH_DAYS` env, default 14) |
| `ROTATE_REFRESH_TOKENS` | `True` — every refresh call issues a new refresh token |
| `BLACKLIST_AFTER_ROTATION` | `True` — the old refresh token is invalidated the moment it's rotated |
| `ALGORITHM` | `HS256`, signed with `SECRET_KEY` |
| `AUTH_HEADER_TYPES` | `("Bearer",)` |

`rest_framework_simplejwt.token_blacklist` is an installed app
(`THIRD_PARTY_APPS`), so blacklisted tokens are persisted in the database —
rotation and logout both write there.

```
POST /api/v1/auth/register/     → creates User (unverified), sends
                                   verify-email + welcome emails, returns
                                   the created user (no tokens yet)
POST /api/v1/auth/login/        → apps.users.views.LoginView, a
                                   TokenObtainPairView subclass wrapping the
                                   response in the standard envelope; returns
                                   {access, refresh}
POST /api/v1/auth/token/refresh/→ rotates refresh, blacklists the old one
POST /api/v1/auth/logout/       → apps.users.views.LogoutView explicitly
                                   blacklists the supplied refresh token
                                   (RefreshToken(...).blacklist())
GET/PATCH /api/v1/auth/me/      → apps.users.views.MeView, IsAuthenticated
```

Every other authenticated endpoint across the API relies on
`rest_framework_simplejwt.authentication.JWTAuthentication`, the sole entry
in `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]`.

## Email verification

Flow implemented by `EmailVerificationToken` (`apps/users/models.py`) +
`issue_email_verification_token` / `confirm_email_verification_token`
(`apps/users/services.py`):

1. `issue_email_verification_token(user)` generates a 32-byte URL-safe random
   token (`secrets.token_urlsafe(32)`) and stores an `EmailVerificationToken`
   row with `expires_at = now + 24h` (`EMAIL_TOKEN_TTL`).
2. `RegisterView` fires this automatically on signup and emails a link
   (`verify_email` template) via `send_email_task.delay(...)`. An
   already-authenticated user can request a fresh one via
   `POST /api/v1/auth/verify-email/`.
3. `POST /api/v1/auth/verify-email/confirm/` (AllowAny — the link is clicked
   from an email client, not necessarily while logged in) calls
   `confirm_email_verification_token(token)`, which:
   - rejects if the token doesn't exist, is already used, or is expired
     (`ErrorCodes.AUTH_OTP_EXPIRED`)
   - marks `used_at`, flips `user.email_verified = True`

Tokens are single-use (`used_at__isnull=True` filter) and never reused after
confirmation, even if the same token string were replayed.

## Phone OTP verification

`PhoneOTP` model + `issue_phone_otp` / `confirm_phone_otp`
(`apps/users/services.py`):

1. `POST /api/v1/auth/verify-phone/` generates a 6-digit numeric code
   (`secrets.randbelow(1_000_000)`, zero-padded), stores only its **hash**
   (`make_password(code)`, Django's password hasher) with a 10-minute TTL
   (`OTP_TTL`), and dispatches it via `send_sms_task.delay(...)`.
2. `POST /api/v1/auth/verify-phone/confirm/` calls `confirm_phone_otp(user, code)`:
   - looks up the user's most recent unused OTP
   - rejects if expired, or if `attempts >= OTP_MAX_ATTEMPTS` (5) —
     `ErrorCodes.AUTH_OTP_INVALID` ("Too many attempts")
   - on a wrong code, increments `attempts` and rejects
     (`check_password(code, record.code_hash)`)
   - on success, marks `used_at` and sets `user.phone_verified = True`

The plaintext OTP is never persisted — only `code_hash` — so a database leak
does not expose valid OTP codes directly.

## Password reset

`PasswordResetToken` model + `issue_password_reset_token` /
`confirm_password_reset` (`apps/users/services.py`):

1. `POST /api/v1/auth/password-reset/` (`PasswordResetRequestView`, AllowAny)
   looks up the user by email. **Regardless of whether the email exists, it
   always returns the same success message** ("If that email exists, a reset
   link has been sent.") — this deliberately prevents account enumeration via
   the reset endpoint. If a user is found, a token is issued
   (`secrets.token_urlsafe(32)`, 1-hour TTL — `PASSWORD_RESET_TTL`) and
   emailed with the `reset_password` template.
2. `POST /api/v1/auth/password-reset-confirm/` calls
   `confirm_password_reset(token, new_password)`: validates the token exactly
   like the email-verification token (unused + not expired), then
   `user.set_password(new_password)` and marks the token used.

## Change password (authenticated)

`POST /api/v1/auth/change-password/` (`ChangePasswordView`,
`IsAuthenticated`) validates the current password via
`ChangePasswordSerializer` (checked against `request.user` in serializer
context) and calls `request.user.set_password(...)`.

## Addresses

`apps/users/views.py:AddressViewSet` (full CRUD, `IsAuthenticated`,
scoped to `Address.objects.filter(user=self.request.user)`) plus a
`set-default` custom action. `Address` is Egypt-specific — it FKs to
`shipping.Governorate`/`shipping.City` (`PROTECT` — a governorate/city
referenced by any saved address cannot be deleted) rather than storing a
free-text region. See `docs/architecture/shipping.md` for the governorate
seed data.

## Password hashing

Standard Django `AUTH_PASSWORD_VALIDATORS` are configured in
`config/settings/base.py`: `UserAttributeSimilarityValidator`,
`MinimumLengthValidator` (8 chars minimum), `CommonPasswordValidator`,
`NumericPasswordValidator`. Password storage uses Django's default hasher
chain (PBKDF2 unless overridden) — no custom hashing logic exists in this
codebase.
