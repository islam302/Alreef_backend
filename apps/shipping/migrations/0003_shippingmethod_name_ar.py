import re

from django.db import migrations, models

ARABIC = re.compile(r"[؀-ۿ]")


def fill_arabic_names(apps, schema_editor):
    """Give every existing method an Arabic name: its own name when staff
    already wrote it in Arabic, otherwise the name for its kind."""
    from apps.shipping.defaults import ARABIC_NAMES

    ShippingMethod = apps.get_model("shipping", "ShippingMethod")
    for method in ShippingMethod.objects.filter(name_ar=""):
        if ARABIC.search(method.name or ""):
            method.name_ar = method.name
        else:
            method.name_ar = ARABIC_NAMES.get(method.method_type, "")
        if method.name_ar:
            method.save(update_fields=["name_ar"])


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0002_default_fees_standard_75_express_100"),
    ]

    operations = [
        migrations.AddField(
            model_name="shippingmethod",
            name="name_ar",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.RunPython(fill_arabic_names, migrations.RunPython.noop),
    ]
