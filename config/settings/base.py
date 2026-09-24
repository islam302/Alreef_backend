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
    "apps.content",
    "apps.admin_api",
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

# Egyptians write their mobile number as 01xxxxxxxxx, which is what both
# frontends ask for. Without a default region, phonenumber_field only accepts
# E.164 (+201xxxxxxxxx) and rejects the format the form itself prompts for —
# so nobody could save an address or register a phone. Stored in E.164.
PHONENUMBER_DEFAULT_REGION = "EG"
PHONENUMBER_DB_FORMAT = "E164"
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
        # A customer waiting for a gateway to confirm polls this every
        # couple of seconds, and each call can reach the provider, so it
        # gets a budget of its own rather than eating the generic one.
        "payment_status": "60/min",
        "search": "60/min",
        "review": "20/min",
        "contact": "5/min",
        # Code-guessing endpoints. The codes themselves are high-entropy, so
        # these are not the only thing standing between an attacker and a
        # balance — but an endpoint that answers "is this code real?" should
        # never be reachable at the generic 300/min authenticated rate.
        "gift_card": "10/min",
        "coupon": "20/min",
        # Anonymous, unauthenticated writes to the events table.
        "analytics": "60/min",
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
        {"name": "Content", "description": "Site content, contact details, pages and FAQ"},
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
        # The safety net for a webhook that never arrived. Fifteen minutes is a
        # compromise: fast enough that a customer who paid is confirmed while
        # they still care, slow enough not to race callbacks that are merely in
        # flight (the task also ignores anything younger than ten minutes).
        "task": "apps.payments.tasks.reconcile_pending_payments_task",
        "schedule": 900.0,  # every 15 minutes
    },
    "expire-stale-payments": {
        # Closes out abandoned checkouts so the dashboard's "needs chasing"
        # filter shows cases that need chasing rather than months of them.
        "task": "apps.payments.tasks.expire_stale_payments_task",
        "schedule": 86400.0,  # daily
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

# This API's own public origin. Payment providers call our webhook endpoints
# out of band, so the URL we register with them must come from configuration
# rather than from a request's Host header — see apps/payments/webhooks.py.
BACKEND_PUBLIC_URL = config("BACKEND_PUBLIC_URL", default="http://localhost:8000")

# ---------------------------------------------------------------------------
# Payment providers
# ---------------------------------------------------------------------------
# Every external gateway is disabled by default and stays disabled until its
# flag *and* all of its mandatory credentials are set — apps/payments/config.py
# enforces that, so a half-filled block never reaches a customer. Run
# `python manage.py check_payments` to see the state of each method.
#
# Webhook URLs to register with each provider are listed in
# docs/payments/PROVIDERS.md. Never put a secret in a VITE_* variable: those
# are compiled into the browser bundle.
PAYMENT_PROVIDERS = {
    # Paymob (Accept). The only real gateway. Serves card and mobile wallet
    # today, and InstaPay too once Paymob enables it — at which point it is this
    # settings block that changes, not any code.
    # Credentials come from Dashboard -> Settings -> Account Info.
    "PAYMOB": {
        "ENABLED": config("PAYMOB_ENABLED", default=False, cast=bool),
        # False keeps the adapter pointed at Paymob's test keys. It changes no
        # URL — Paymob switches environment by key, not by host — but it is
        # recorded on every payment so a live transaction created with a test
        # key is visible rather than merely wrong.
        "LIVE": config("PAYMOB_LIVE", default=False, cast=bool),
        # Intention API (v1): `Authorization: Token <secret key>`.
        "SECRET_KEY": config("PAYMOB_SECRET_KEY", default=""),
        # Used to build the hosted checkout URL the customer is sent to.
        "PUBLIC_KEY": config("PAYMOB_PUBLIC_KEY", default=""),
        # Legacy Accept API key. Refund, void and transaction inquiry
        # authenticate with this, not with the secret key.
        "API_KEY": config("PAYMOB_API_KEY", default=""),
        # Verifies every callback. Without it we cannot trust a payment
        # notification, so config.py treats it as mandatory.
        "HMAC_SECRET": config("PAYMOB_HMAC_SECRET", default=""),
        # Visa / Mastercard / Meeza.
        "INTEGRATION_ID_CARD": config("PAYMOB_INTEGRATION_ID_CARD", default=""),
        # Vodafone Cash, Etisalat, Orange, WE.
        "INTEGRATION_ID_WALLET": config("PAYMOB_INTEGRATION_ID_WALLET", default=""),
        # Paymob lists InstaPay as coming soon. Leave this empty until it is
        # live on your account: while it is empty, InstaPay is served by the
        # manual-transfer route below, and setting it switches automatically.
        "INTEGRATION_ID_INSTAPAY": config("PAYMOB_INTEGRATION_ID_INSTAPAY", default=""),
        # Only needed for the legacy iframe display mode.
        "IFRAME_ID": config("PAYMOB_IFRAME_ID", default=""),
        "BASE_URL": config("PAYMOB_BASE_URL", default="https://accept.paymob.com"),
        "TIMEOUT_SECONDS": config("PAYMOB_TIMEOUT_SECONDS", default=20, cast=int),
    },
    # InstaPay, as a bank transfer the customer makes and staff confirm.
    #
    # There is no API here and there is no API to have: InstaPay is the Central
    # Bank of Egypt's instant transfer network, not a payment gateway, and it
    # publishes no merchant acceptance API. This is how Egyptian shops are paid
    # by InstaPay today — publish an address, take the transfer, confirm it.
    # See docs/payments/instapay.md.
    "INSTAPAY": {
        "ENABLED": config("INSTAPAY_ENABLED", default=False, cast=bool),
        # The shop's InstaPay address (IPA), e.g. alreef@instapay. Shown to
        # customers — it is a published payment address, not a credential.
        "ADDRESS": config("INSTAPAY_ADDRESS", default=""),
        # The account name the customer will see in their banking app, so a
        # cautious customer can check it matches before transferring.
        "ACCOUNT_NAME": config("INSTAPAY_ACCOUNT_NAME", default=""),
        # How long the order waits for the transfer before staff should chase it.
        "CONFIRM_WINDOW_HOURS": config("INSTAPAY_CONFIRM_WINDOW_HOURS", default=24, cast=int),
    },
    # Cash on delivery. No external API: the courier collects, and staff mark
    # it collected. Every number here is configurable because they are
    # commercial decisions, not code.
    "COD": {
        "ENABLED": config("COD_ENABLED", default=True, cast=bool),
        "FEE_AMOUNT": config("COD_FEE_AMOUNT", default="15.00"),
        "MIN_ORDER_AMOUNT": config("COD_MIN_ORDER_AMOUNT", default="0.00"),
        # 0 means no ceiling.
        "MAX_ORDER_AMOUNT": config("COD_MAX_ORDER_AMOUNT", default="50000.00"),
        # Comma-separated ShippingMethod ids. Empty means any method, which is
        # right until a carrier that will not handle cash is added.
        "ALLOWED_SHIPPING_METHOD_IDS": config("COD_ALLOWED_SHIPPING_METHOD_IDS", default=""),
    },
}

# How stale a webhook may be before it is recorded but not applied. Providers
# retry for hours, so an event about a payment we have since moved on from is
# normal; applying it blindly would walk the payment backwards.
PAYMENT_WEBHOOK_MAX_AGE_HOURS = config("PAYMENT_WEBHOOK_MAX_AGE_HOURS", default=72, cast=int)

# Currencies the payment layer will accept. Anything else is rejected before a
# provider is contacted.
PAYMENT_ALLOWED_CURRENCIES = [
    code.strip().upper()
    for code in config("PAYMENT_ALLOWED_CURRENCIES", default="EGP").split(",")
    if code.strip()
]

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

# The OpenAPI schema documents every route, admin ones included — a complete
# map of the back office, served to anyone who types the URL. It is genuinely
# useful in development and on a staging box, so it stays a switch rather than
# a deletion; it just defaults to off wherever DEBUG is off.
#
# Staff-gating it instead does not work here: DRF authenticates with JWT only,
# so a signed-in staff member browsing to /api/docs/ would be rejected like
# anyone else and the page would be broken rather than protected.
API_DOCS_ENABLED = config("API_DOCS_ENABLED", default=DEBUG, cast=bool)

SITE_NAME = config("SITE_NAME", default="Alreef")
IDEMPOTENCY_KEY_TTL_SECONDS = config("IDEMPOTENCY_KEY_TTL_SECONDS", default=86400, cast=int)
ABANDONED_CART_HOURS = config("ABANDONED_CART_HOURS", default=6, cast=int)

# The most units of one variant a single cart line may hold. There was no ceiling
# at all, which meant a cart happily accepted a two-billion-unit line, and
# anything at or above 2**31 was refused by Postgres rather than by validation —
# surfacing as a 500 instead of a 400. The cap is what the shop would actually
# ship in one order, not what an integer column can hold.
MAX_CART_ITEM_QUANTITY = config("MAX_CART_ITEM_QUANTITY", default=100, cast=int)

# Bounds on the free-form `metadata` object accepted by the public event
# tracker. It is unauthenticated, so without limits an anonymous caller could
# store roughly a megabyte of arbitrary JSON per request — measured at 1,011,890
# characters — and do it 60 times a minute per address.
ANALYTICS_METADATA_MAX_CHARS = config("ANALYTICS_METADATA_MAX_CHARS", default=4000, cast=int)
ANALYTICS_METADATA_MAX_KEYS = config("ANALYTICS_METADATA_MAX_KEYS", default=40, cast=int)
ANALYTICS_METADATA_MAX_DEPTH = config("ANALYTICS_METADATA_MAX_DEPTH", default=3, cast=int)
EXPIRATION_ALERT_DAYS = [7, 14, 30]
