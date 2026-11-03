from django.db import migrations

from apps.content.policy_pages import rewrite_refund_policy_v3


def forwards(apps, schema_editor):
    # Only a policy nobody has edited is rewritten; an edited one stays as it is.
    rewrite_refund_policy_v3(apps.get_model("content", "Page"), apps.get_model("content", "PageSection"))


class Migration(migrations.Migration):
    dependencies = [("content", "0007_refund_policy_deposit_wording")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
