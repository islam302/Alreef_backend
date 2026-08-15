from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Core"

    def ready(self):
        # Makes every ModelSerializer render djmoney MoneyField model fields
        # as a plain decimal amount instead of crashing on `str(Money(...))`.
        from djmoney.contrib.django_rest_framework import register_money_field

        register_money_field()
