from django.db import migrations


def remove_unverified_founding_year(apps, schema_editor):
    SiteSettings = apps.get_model("content", "SiteSettings")
    SiteSettings.objects.update(founded_year=None)


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(remove_unverified_founding_year, migrations.RunPython.noop),
    ]
