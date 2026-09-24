from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False
ALLOWED_HOSTS = config("ALLOWED_HOSTS", cast=Csv())

# base.py computes API_DOCS_ENABLED as `config("API_DOCS_ENABLED", default=DEBUG)`,
# and that DEBUG is the *environment's* value, read before this module forces it
# to False. So an environment carrying DEBUG=True — a copied .env, a leftover
# platform variable — got DEBUG correctly disabled here while silently keeping
# the docs on, publishing a complete map of every route including the admin API.
# Re-evaluate it against the DEBUG that actually applies: off unless the operator
# asks for it by name, which is what the note in base.py always claimed.
API_DOCS_ENABLED = config("API_DOCS_ENABLED", default=False, cast=bool)

# Fail closed: never allow a production boot (gunicorn, celery, manage.py) to
# proceed silently on the insecure dev fallback key from base.py.
if SECRET_KEY == "django-insecure-change-me-in-env":  # noqa: F405
    raise ImproperlyConfigured(
        "SECRET_KEY is unset or still the insecure development default. "
        "Set a real, random SECRET_KEY environment variable before running in production."
    )

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

DATABASES["default"]["CONN_MAX_AGE"] = 600  # noqa: F405

SENTRY_DSN = config("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.redis import RedisIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration(), RedisIntegration()],
        traces_sample_rate=config("SENTRY_TRACES_SAMPLE_RATE", default=0.1, cast=float),
        send_default_pii=False,
        environment=config("ENVIRONMENT", default="production"),
    )

STORAGES = {
    "default": {
        "BACKEND": config(
            "DEFAULT_FILE_STORAGE",
            default="django.core.files.storage.FileSystemStorage",
        )
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

if config("USE_S3", default=False, cast=bool):
    STORAGES["default"]["BACKEND"] = "storages.backends.s3.S3Storage"
    AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID", default="")
    AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY", default="")
    AWS_STORAGE_BUCKET_NAME = config("AWS_STORAGE_BUCKET_NAME", default="")
    AWS_S3_REGION_NAME = config("AWS_S3_REGION_NAME", default="eu-central-1")
    AWS_S3_CUSTOM_DOMAIN = config("AWS_S3_CUSTOM_DOMAIN", default="")
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
