from decouple import Csv, UndefinedValueError, config
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

def _require(name, cast=None):
    """
    Read a value that must be supplied explicitly in production.

    Several of these have a localhost fallback in base.py, which is right for a
    developer and wrong here: an operator who forgot one got a working boot and a
    silently broken deployment. `FRONTEND_URL` is the clearest case — it builds
    the links inside password-reset and verification emails, so the default sent
    customers a link to their own machine.

    Failing the boot is the point. A container that will not start is a problem
    you find in a deploy log; a container serving localhost links is one you find
    from a customer.
    """
    try:
        return config(name, cast=cast) if cast is not None else config(name)
    except UndefinedValueError as exc:
        raise ImproperlyConfigured(
            f"{name} is not set. Production requires it explicitly, because the "
            f"development fallback in base.py points at localhost and would ship."
        ) from exc


# Origins and public URLs: no silent inheritance of the localhost defaults.
CORS_ALLOWED_ORIGINS = _require("CORS_ALLOWED_ORIGINS", Csv())
CSRF_TRUSTED_ORIGINS = _require("CSRF_TRUSTED_ORIGINS", Csv())
FRONTEND_URL = _require("FRONTEND_URL")
BACKEND_PUBLIC_URL = _require("BACKEND_PUBLIC_URL")

# Off by default here. Authentication is a Bearer header, not a cookie, so
# cross-origin credentials buy nothing — and in the intended topology everything
# sits behind one edge proxy on one origin, where CORS never applies at all.
# Left switchable for a deliberate split-origin deployment, which is the only
# case where guest carts need their session cookie to cross origins.
CORS_ALLOW_CREDENTIALS = config("CORS_ALLOW_CREDENTIALS", default=False, cast=bool)

# Mail must actually leave the building.
#
# base.py defaults to the console backend, which is correct for development and
# was reaching production untouched. Two consequences, and the second is the
# worse one: password reset, email verification and order confirmation all went
# nowhere, so a customer who forgot their password had no way back into their
# account — and the console backend writes the whole message, including the
# single-use reset link, to stdout, which becomes the container log. Anyone able
# to read logs could take over any account that requested a reset.
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="")

_BACKENDS_THAT_DO_NOT_SEND = ("console", "locmem", "dummy", "filebased")
_allow_unsendable = config("ALLOW_UNSENDABLE_EMAIL_BACKEND", default=False, cast=bool)

if any(name in EMAIL_BACKEND for name in _BACKENDS_THAT_DO_NOT_SEND) and not _allow_unsendable:
    raise ImproperlyConfigured(
        f"EMAIL_BACKEND is '{EMAIL_BACKEND}', which does not send mail. In production this "
        "silently breaks password reset, email verification and order confirmation, and writes "
        "reset links to the logs. Configure a real backend, or set "
        "ALLOW_UNSENDABLE_EMAIL_BACKEND=True if this is a stack that deliberately sends nothing "
        "(CI, a smoke-test environment)."
    )

if EMAIL_BACKEND.endswith("smtp.EmailBackend") and not EMAIL_HOST:
    raise ImproperlyConfigured(
        "EMAIL_BACKEND is the SMTP backend but EMAIL_HOST is empty, so every send would fail at "
        "delivery time rather than at boot. Set EMAIL_HOST (and the credentials it needs)."
    )

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Persistent connections, and — crucially — probed before reuse. Holding a
# connection for ten minutes without checking it means that after a database
# restart, a failover, or a server-side idle timeout, a worker hands a dead
# socket to a customer's request. Django added CONN_HEALTH_CHECKS in 4.1 for
# exactly this; leaving it off turns routine maintenance into a burst of 500s.
DATABASES["default"]["CONN_MAX_AGE"] = 600  # noqa: F405
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True  # noqa: F405

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
