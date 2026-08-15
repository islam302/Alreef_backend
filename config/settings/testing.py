from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key"

DATABASES["default"]["NAME"] = "test_alreef"  # noqa: F405
DATABASES["default"]["HOST"] = "localhost"  # noqa: F405
DATABASES["default"]["PORT"] = "5433"  # noqa: F405
DATABASES["default"]["USER"] = config("DB_USER", default="alreef")  # noqa: F405
DATABASES["default"]["PASSWORD"] = config("DB_PASSWORD", default="alreef")  # noqa: F405

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SMS_PROVIDER = "console"  # noqa: F405
WHATSAPP_PROVIDER = "console"  # noqa: F405

CELERY_TASK_ALWAYS_EAGER = True  # noqa: F405
CELERY_TASK_EAGER_PROPAGATES = True  # noqa: F405

CACHES = {  # noqa: F405
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

MEDIA_ROOT = BASE_DIR / "test_media"  # noqa: F405
