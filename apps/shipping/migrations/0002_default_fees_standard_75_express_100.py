from django.db import migrations


def apply(apps, schema_editor):
    # The rule lives in apps/shipping/defaults.py, shared with seed_data.
    from apps.shipping.defaults import apply_default_fees

    apply_default_fees(
        apps.get_model("shipping", "Governorate"),
        apps.get_model("shipping", "ShippingZone"),
        apps.get_model("shipping", "ShippingMethod"),
        apps.get_model("shipping", "ShippingRate"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0001_initial"),
    ]

    operations = [
        # Not reversible: the fees it replaced were per zone and are not kept.
        migrations.RunPython(apply, migrations.RunPython.noop),
    ]
