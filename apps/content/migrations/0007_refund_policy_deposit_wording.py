from django.db import migrations

from apps.content.policy_pages import rename_deposit_section


def forwards(apps, schema_editor):
    rename_deposit_section(apps.get_model("content", "Page"), apps.get_model("content", "PageSection"))


class Migration(migrations.Migration):
    dependencies = [("content", "0006_refund_policy_deposit")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
