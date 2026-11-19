from django.db import migrations

from apps.content.policy_pages import disclose_analytics


def forwards(apps, schema_editor):
    # The storefront now uses Google Analytics; say so in the privacy policy.
    disclose_analytics(apps.get_model("content", "Page"), apps.get_model("content", "PageSection"))


class Migration(migrations.Migration):
    dependencies = [("content", "0010_site_sold_count_min")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
