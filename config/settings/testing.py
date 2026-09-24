from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key"

# Read from the environment, like USER and PASSWORD below. These two were
# hardcoded to the host port that docker-compose.yml publishes for local work,
# which meant CI could never connect: a runner's Postgres service listens on
# 5432, and no environment variable could talk this module out of 5433.
# The defaults keep `pytest` working with no configuration on a developer's
# machine, which is what the hardcoding was for.
# NAME stays fixed: pytest-django derives the real test database by prefixing
# `test_`, and letting the environment change it would quietly rename the
# database developers already have locally. Only where to reach the server was
# ever the problem.
DATABASES["default"]["NAME"] = "test_alreef"  # noqa: F405
DATABASES["default"]["HOST"] = config("DB_HOST", default="localhost")  # noqa: F405
DATABASES["default"]["PORT"] = config("DB_PORT", default="5433")  # noqa: F405
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
