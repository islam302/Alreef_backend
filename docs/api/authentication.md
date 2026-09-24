# Authentication

Base path: `/api/v1/auth/` (registration, login, tokens, verification) and `/api/v1/` (addresses).

Source: `apps/users/urls_auth.py`, `apps/users/urls.py`, `apps/users/views.py`, `apps/users/serializers.py`, `apps/users/models.py`.

All responses use the standard envelope (`{"success", "message", "data", "errors"}`) documented in `docs/api/errors.md`. `POST` bodies are JSON. Endpoints under `throttle_scope = "auth"` are rate-limited to **20/min** per client (`DEFAULT_THROTTLE_RATES` in `config/settings/base.py`).

## Token model

JWT via `rest_framework_simplejwt`, configured in `config/settings/base.py` (`SIMPLE_JWT`):

- **Access token lifetime**: 15 minutes (`JWT_ACCESS_MINUTES`, default 15)
- **Refresh token lifetime**: 14 days (`JWT_REFRESH_DAYS`, default 14)
- **Rotation**: `ROTATE_REFRESH_TOKENS = True` — every refresh call issues a brand-new refresh token
- **Blacklisting**: `BLACKLIST_AFTER_ROTATION = True` — the old refresh token is blacklisted the moment it's rotated; `rest_framework_simplejwt.token_blacklist` is installed
- **Header**: `Authorization: Bearer <access_token>`
- **Login identifier**: `email` (`USER_ID_FIELD = "id"`, `USERNAME_FIELD = "email"` on the custom `User` model)

## POST /api/v1/auth/register/

Auth: none (`AllowAny`). Throttle: `auth`.

Creates a `User`, sends a verification email (Celery task `send_email_task`) and a welcome email. Does **not** log the user in — call `/auth/login/` after registering.

Request body (`RegisterSerializer`):
```json
{
  "email": "sara.mostafa@example.com",
  "phone": "+201001234567",
  "first_name": "Sara",
  "last_name": "Mostafa",
  "password": "StrongPass123"
}
```
`phone` is optional. `password` is validated with Django's `AUTH_PASSWORD_VALIDATORS` (min length 8, not too common, not fully numeric, not too similar to user attributes).

Success `201`:
```json
{
  "success": true,
  "message": "Account created. Please verify your email.",
  "data": {
    "id": 42,
    "email": "sara.mostafa@example.com",
    "phone": "+201001234567",
    "first_name": "Sara",
    "last_name": "Mostafa",
    "full_name": "Sara Mostafa",
    "profile_image": null,
    "email_verified": false,
    "phone_verified": false,
    "date_joined": "2026-08-14T10:00:00Z",
    "last_login": null
  },
  "errors": null
}
```

Errors: `AUTH_EMAIL_ALREADY_EXISTS` (409), `AUTH_PHONE_ALREADY_EXISTS` (409), `VALIDATION_ERROR` (400, weak password / missing fields).

## POST /api/v1/auth/login/

Auth: none. Throttle: `auth`. Implemented as `LoginView(TokenObtainPairView)` with a custom `LoginSerializer`.

Request:
```json
{ "email": "sara.mostafa@example.com", "password": "StrongPass123" }
```

Success `200`:
```json
{
  "success": true,
  "message": "Login successful.",
  "data": {
    "refresh": "<refresh_jwt>",
    "access": "<access_jwt>",
    "user": {
      "id": 42, "email": "sara.mostafa@example.com", "phone": "+201001234567",
      "first_name": "Sara", "last_name": "Mostafa", "full_name": "Sara Mostafa",
      "profile_image": null, "email_verified": false, "phone_verified": false,
      "date_joined": "2026-08-14T10:00:00Z", "last_login": "2026-08-14T10:05:00Z"
    }
  },
  "errors": null
}
```

Errors: `AUTH_INVALID_CREDENTIALS` (401), `AUTH_ACCOUNT_DISABLED` (403, `is_active=False`). Note: the API does **not** require `email_verified` to log in — verification is enforced by callers/business logic elsewhere if needed, not by `LoginView` itself.

## POST /api/v1/auth/logout/

Auth: `IsAuthenticated` (Bearer access token). Blacklists the given refresh token.

Request:
```json
{ "refresh": "<refresh_jwt>" }
```

Success `200`: `{"success": true, "message": "Logged out.", "data": null, "errors": null}`

Errors: `AUTH_TOKEN_INVALID` (401, already blacklisted or malformed).

## POST /api/v1/auth/token/refresh/

Auth: none. Standard SimpleJWT refresh, wrapped in the envelope. Because rotation+blacklist is on, the response always contains a **new** refresh token — the old one is now invalid.

Request: `{ "refresh": "<refresh_jwt>" }`

Success `200`:
```json
{ "success": true, "message": "Token refreshed.", "data": { "access": "<new_access_jwt>", "refresh": "<new_refresh_jwt>" }, "errors": null }
```

Errors: `AUTH_TOKEN_EXPIRED` (401).

## GET /api/v1/auth/me/

Auth: `IsAuthenticated`.

Success `200` (`MeSerializer`):
```json
{
  "success": true,
  "message": "Success",
  "data": {
    "id": 42, "email": "sara.mostafa@example.com", "phone": "+201001234567",
    "first_name": "Sara", "last_name": "Mostafa", "full_name": "Sara Mostafa",
    "profile_image": null, "email_verified": false, "phone_verified": false,
    "date_joined": "2026-08-14T10:00:00Z", "last_login": "2026-08-14T10:05:00Z",
    "profile": { "date_of_birth": null, "gender": "", "marketing_consent": false, "language": "ar", "currency": "EGP" }
  },
  "errors": null
}
```

## PATCH /api/v1/auth/me/

Auth: `IsAuthenticated`. Partial update of `first_name`, `last_name`, `phone`, `profile_image`, and nested `profile` (`date_of_birth`, `gender`, `marketing_consent`, `language`, `currency`). `email`, `email_verified`, `phone_verified`, `date_joined`, `last_login` are read-only.

Request:
```json
{ "first_name": "Sara", "profile": { "marketing_consent": true, "language": "ar" } }
```

Success `200`: same shape as GET, message `"Profile updated."`.

## POST /api/v1/auth/change-password/

Auth: `IsAuthenticated`.

Request:
```json
{ "old_password": "StrongPass123", "new_password": "EvenStronger456" }
```

Success `200`: `"Password changed."` Errors: `AUTH_INVALID_CREDENTIALS` (401, wrong old password), `VALIDATION_ERROR` (weak new password).

## POST /api/v1/auth/password-reset/

Auth: none. Throttle: `auth`. Always returns success regardless of whether the email exists (prevents account enumeration) and emails a reset link containing a one-time token when it does.

Request: `{ "email": "sara.mostafa@example.com" }`

Success `200`: `"If that email exists, a reset link has been sent."`

## POST /api/v1/auth/password-reset-confirm/

Auth: none.

Request:
```json
{ "token": "<reset_token>", "new_password": "EvenStronger456" }
```

Success `200`: `"Password has been reset."` The token is a `PasswordResetToken` row; expired/used tokens raise a validation-style error from `apps/users/services.py`.

## POST /api/v1/auth/verify-email/

Auth: `IsAuthenticated`. Resends the verification email (no body). No-op success if already verified.

Success `200`: `"Verification email sent."` (or `"Email already verified."`)

## POST /api/v1/auth/verify-email/confirm/

Auth: none. Accepts the token from the emailed link either as a JSON body or `?token=` query param.

Request: `{ "token": "<verify_token>" }`

Success `200`: `"Email verified."`

## POST /api/v1/auth/verify-phone/

Auth: `IsAuthenticated`. Throttle: `auth`. Sends a numeric OTP by SMS (`send_sms_task`) to `phone` in the body, or the user's `phone` on file if omitted.

Request: `{ "phone": "+201001234567" }` (optional)

Success `200`: `"OTP sent."` Errors: `VALIDATION_ERROR` (400, no phone on file and none supplied).

## POST /api/v1/auth/verify-phone/confirm/

Auth: `IsAuthenticated`.

Request: `{ "code": "483920" }` (4-6 digits)

Success `200`: `"Phone verified."` Errors: `AUTH_OTP_INVALID` (400), `AUTH_OTP_EXPIRED` (400).

---

## Addresses — `/api/v1/addresses/`

`AddressViewSet` (`ModelViewSet`), auth: `IsAuthenticated`, scoped to `request.user`. Registered via `DefaultRouter` under the un-prefixed `apps.users.urls`.

Fields (`AddressSerializer`): `id, label, full_name, phone, governorate, governorate_name, city, city_name, district, street, building, floor, apartment, landmark, postal_code, notes, is_default, created_at, updated_at`. `label` is one of `home | work | other`. `governorate`/`city` are FK ids referencing `apps.shipping` (`Governorate`/`City` — see `docs/api/shipping.md`). Shipping **prices are never stored on the address**.

- `GET /api/v1/addresses/` — list current user's addresses, ordered `-is_default, -created_at`. Not paginated (returns a plain array in `data`).
- `POST /api/v1/addresses/` — create. `201`.
  ```json
  {
    "label": "home", "full_name": "Sara Mostafa", "phone": "+201001234567",
    "governorate": 1, "city": 3, "district": "Nasr City", "street": "Makram Ebeid St.",
    "building": "12", "floor": "4", "apartment": "9", "landmark": "Near City Stars",
    "postal_code": "11371", "notes": "", "is_default": true
  }
  ```
- `GET /api/v1/addresses/{id}/` — retrieve.
- `PUT` / `PATCH /api/v1/addresses/{id}/` — update.
- `DELETE /api/v1/addresses/{id}/` — delete, `204`.
- `POST /api/v1/addresses/{id}/set-default/` — sets `is_default=True` on this address (does not unset other addresses' flags in code — verify against business need).

Errors: `VALIDATION_ERROR` (400), `NOT_FOUND` (404, address not owned by user or missing).
