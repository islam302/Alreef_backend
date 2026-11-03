from django.db import migrations


def add_section(apps, schema_editor):
    # Text in apps/content/policy_pages.py. Added only if the refund policy
    # exists and has no deposit section yet; nothing else on the page changes.
    from apps.content.policy_pages import add_deposit_section

    add_deposit_section(apps.get_model("content", "Page"), apps.get_model("content", "PageSection"))


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0005_policy_pages"),
    ]

    operations = [
        migrations.RunPython(add_section, migrations.RunPython.noop),
    ]
