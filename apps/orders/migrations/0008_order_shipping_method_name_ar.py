from django.db import migrations, models


def backfill(apps, schema_editor):
    """Past orders take the Arabic name of the method they were placed with
    (matched by name, as the shipment form matches them)."""
    Order = apps.get_model("orders", "Order")
    ShippingMethod = apps.get_model("shipping", "ShippingMethod")
    for method in ShippingMethod.objects.exclude(name_ar=""):
        Order.objects.filter(shipping_method_name=method.name, shipping_method_name_ar="").update(
            shipping_method_name_ar=method.name_ar
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0007_return_policy_food"),
        ("shipping", "0003_shippingmethod_name_ar"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="shipping_method_name_ar",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
