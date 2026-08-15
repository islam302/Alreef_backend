"""
Base Django settings for the Alreef Egyptian E-Commerce platform.
Shared by development / production / testing settings.
"""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config("SECRET_KEY", default="django-insecure-change-me-in-env")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "django_celery_beat",
    "django_celery_results",
    "mptt",
    "phonenumber_field",
    "health_check",
    "health_check.db",
    "health_check.cache",
    "health_check.storage",
]

LOCAL_APPS = [
    "apps.core",
    "apps.users",
    "apps.catalog",
    "apps.categories",
    "apps.brands",
    "apps.attributes",
    "apps.inventory",
    "apps.carts",
    "apps.checkout",
    "apps.orders",
    "apps.payments",
    "apps.shipping",
    "apps.coupons",
    "apps.promotions",
    "apps.reviews",
    "apps.wishlists",
    "apps.subscriptions",
    "apps.loyalty",
    "apps.gift_cards",
    "apps.notifications",
    "apps.search",
    "apps.analytics",
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "apps.audit.middleware.AuditLogMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME", default="alreef"),
        "USER": config("DB_USER", default="alreef"),
        "PASSWORD": config("DB_PASSWORD", default="alreef"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
        "CONN_MAX_AGE": config("DB_CONN_MAX_AGE", default=60, cast=int),
        "ATOMIC_REQUESTS": False,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# I18N / Egypt defaults
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "ar"
LANGUAGES = [("ar", "Arabic"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True

DEFAULT_COUNTRY = "EG"
DEFAULT_CURRENCY = "EGP"
CURRENCIES = ["EGP", "USD"]

# ---------------------------------------------------------------------------
# Static / Media
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        # Baseline safety net applied to EVERY endpoint regardless of whether
        # it sets throttle_scope — ScopedRateThrottle is a no-op on views
        # without a scope, so anon/user rates ensure nothing is ever fully
        # unthrottled.
        "anon": "60/min",
        "user": "300/min",
        "auth": "20/min",
        # Tighter scope for token/OTP *confirmation* endpoints, where the
        # attacker is guessing a secret rather than just retrying a login.
        "auth_confirm": "10/min",
        "checkout": "30/min",
        "payment_webhook": "120/min",
        "search": "60/min",
        "review": "20/min",
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.custom_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "DATETIME_FORMAT": "iso-8601",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_MINUTES", default=15, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_DAYS", default=14, cast=int)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Alreef E-Commerce API",
    "DESCRIPTION": (
        "Production API for the Alreef Egyptian e-commerce platform "
        "(honey, food, natural products, grocery). All endpoints are "
        "versioned under /api/v1/."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": True,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]",
    "SERVERS": [{"url": "/", "description": "Current environment"}],
    "TAGS": [
        {"name": "Auth", "description": "Registration, login, JWT tokens"},
        {"name": "Users", "description": "User profile management"},
        {"name": "Categories", "description": "Product categories"},
        {"name": "Brands", "description": "Product brands"},
        {"name": "Products", "description": "Catalog browsing and details"},
        {"name": "Search", "description": "Full-text search & filters"},
        {"name": "Cart", "description": "Shopping cart"},
        {"name": "Wishlist", "description": "Customer wishlists"},
        {"name": "Addresses", "description": "Customer addresses"},
        {"name": "Coupons", "description": "Discount coupons"},
        {"name": "Promotions", "description": "Automatic promotions"},
        {"name": "Shipping", "description": "Shipping zones, methods and rates"},
        {"name": "Checkout", "description": "Checkout session and totals"},
        {"name": "Payments", "description": "Payment creation, status, webhooks"},
        {"name": "Orders", "description": "Order lifecycle"},
        {"name": "Returns", "description": "Returns & refunds"},
        {"name": "Reviews", "description": "Product reviews"},
        {"name": "Loyalty", "description": "Loyalty points"},
        {"name": "Gift Cards", "description": "Gift cards & store credit"},
        {"name": "Subscriptions", "description": "Recurring product subscriptions"},
        {"name": "Notifications", "description": "User notifications"},
        {"name": "Admin", "description": "Administrative operations"},
        {"name": "Webhooks", "description": "Inbound provider webhooks"},
        {"name": "Health", "description": "Health checks"},
    ],
}

# ---------------------------------------------------------------------------
# CORS / CSRF / Security
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:3000", cast=Csv())
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="http://localhost:3000", cast=Csv())

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # frontend JS reads csrftoken for form posts if used
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------------------
# Cache / Redis
# ---------------------------------------------------------------------------
REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "alreef",
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default="django-db")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "apps.notifications.tasks.*": {"queue": "notifications"},
    "apps.inventory.tasks.*": {"queue": "inventory"},
    "apps.analytics.tasks.*": {"queue": "analytics"},
    "apps.payments.tasks.*": {"queue": "payments"},
}

CELERY_BEAT_SCHEDULE = {
    "release-expired-stock-reservations": {
        "task": "apps.inventory.tasks.release_expired_reservations_task",
        "schedule": 300.0,  # every 5 minutes
    },
    "deactivate-expired-batches": {
        "task": "apps.inventory.tasks.deactivate_expired_batches_task",
        "schedule": 3600.0,  # hourly
    },
    "notify-expiration-alerts": {
        "task": "apps.notifications.tasks.notify_expiration_alerts_task",
        "schedule": 86400.0,  # daily
    },
    "process-abandoned-carts": {
        "task": "apps.notifications.tasks.process_abandoned_carts_task",
        "schedule": 3600.0,  # hourly
    },
    "reconcile-pending-payments": {
        "task": "apps.payments.tasks.reconcile_pending_payments_task",
        "schedule": 900.0,  # every 15 minutes
    },
    "process-due-subscriptions": {
        "task": "apps.subscriptions.tasks.process_due_subscriptions_task",
        "schedule": 86400.0,  # daily
    },
    "expire-stale-loyalty-points": {
        "task": "apps.loyalty.tasks.expire_stale_points_task",
        "schedule": 86400.0,  # daily
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "sensitive": {"()": "apps.core.logging_filters.SensitiveDataFilter"},
    },
    "formatters": {
        "json": {"()": "apps.core.logging_filters.JsonFormatter"},
        "simple": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": config("LOG_FORMAT", default="simple"),
            "filters": ["sensitive"],
        },
    },
    "root": {"handlers": ["console"], "level": config("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": config("DJANGO_LOG_LEVEL", default="INFO"), "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# Email (see docs/architecture/notifications.md)
# ---------------------------------------------------------------------------
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="Alreef <no-reply@alreef.example>")

# ---------------------------------------------------------------------------
# SMS / WhatsApp provider abstraction (see apps.notifications.providers)
# ---------------------------------------------------------------------------
SMS_PROVIDER = config("SMS_PROVIDER", default="console")  # console | vonage | twilio | msegat
SMS_API_KEY = config("SMS_API_KEY", default="")
SMS_API_SECRET = config("SMS_API_SECRET", default="")
SMS_SENDER_ID = config("SMS_SENDER_ID", default="ALREEF")

WHATSAPP_PROVIDER = config("WHATSAPP_PROVIDER", default="console")  # console | meta_cloud_api
WHATSAPP_PHONE_NUMBER_ID = config("WHATSAPP_PHONE_NUMBER_ID", default="")
WHATSAPP_ACCESS_TOKEN = config("WHATSAPP_ACCESS_TOKEN", default="")

FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:3000")

# ---------------------------------------------------------------------------
# Payment providers (credentials only — no logic here). See apps.payments.
# ---------------------------------------------------------------------------
PAYMENT_PROVIDERS = {
    "PAYMOB": {
        "ENABLED": config("PAYMOB_ENABLED", default=False, cast=bool),
        "API_KEY": config("PAYMOB_API_KEY", default=""),
        "INTEGRATION_ID_CARD": config("PAYMOB_INTEGRATION_ID_CARD", default=""),
        "INTEGRATION_ID_WALLET": config("PAYMOB_INTEGRATION_ID_WALLET", default=""),
        "IFRAME_ID": config("PAYMOB_IFRAME_ID", default=""),
        "HMAC_SECRET": config("PAYMOB_HMAC_SECRET", default=""),
        "BASE_URL": config("PAYMOB_BASE_URL", default="https://accept.paymob.com/api"),
    },
    "FAWRY": {
        "ENABLED": config("FAWRY_ENABLED", default=False, cast=bool),
        "MERCHANT_CODE": config("FAWRY_MERCHANT_CODE", default=""),
        "SECURITY_KEY": config("FAWRY_SECURITY_KEY", default=""),
        "BASE_URL": config("FAWRY_BASE_URL", default="https://atfawry.fawrystaging.com"),
    },
    "VALU": {
        "ENABLED": config("VALU_ENABLED", default=False, cast=bool),
        "API_KEY": config("VALU_API_KEY", default=""),
        "BASE_URL": config("VALU_BASE_URL", default=""),
    },
    "COD": {
        "ENABLED": config("COD_ENABLED", default=True, cast=bool),
        "FEE_AMOUNT": config("COD_FEE_AMOUNT", default="15.00"),
        "MIN_ORDER_AMOUNT": config("COD_MIN_ORDER_AMOUNT", default="0.00"),
        "MAX_ORDER_AMOUNT": config("COD_MAX_ORDER_AMOUNT", default="50000.00"),
    },
}

# ---------------------------------------------------------------------------
# Shipping providers
# ---------------------------------------------------------------------------
SHIPPING_PROVIDERS = {
    "BOSTA": {
        "ENABLED": config("BOSTA_ENABLED", default=False, cast=bool),
        "API_KEY": config("BOSTA_API_KEY", default=""),
        "BASE_URL": config("BOSTA_BASE_URL", default="https://app.bosta.co/api/v2"),
    },
    "ARAMEX": {
        "ENABLED": config("ARAMEX_ENABLED", default=False, cast=bool),
        "USERNAME": config("ARAMEX_USERNAME", default=""),
        "PASSWORD": config("ARAMEX_PASSWORD", default=""),
        "ACCOUNT_NUMBER": config("ARAMEX_ACCOUNT_NUMBER", default=""),
    },
}

EGYPT_VAT_RATE = Decimal(config("EGYPT_VAT_RATE", default="0.14"))

SITE_NAME = config("SITE_NAME", default="Alreef")
IDEMPOTENCY_KEY_TTL_SECONDS = config("IDEMPOTENCY_KEY_TTL_SECONDS", default=86400, cast=int)
ABANDONED_CART_HOURS = config("ABANDONED_CART_HOURS", default=6, cast=int)
EXPIRATION_ALERT_DAYS = [7, 14, 30]
