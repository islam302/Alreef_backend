from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS += ["django_extensions"]  # noqa: F405

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"  # noqa: F405
SMS_PROVIDER = "console"  # noqa: F405
WHATSAPP_PROVIDER = "console"  # noqa: F405

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
)
