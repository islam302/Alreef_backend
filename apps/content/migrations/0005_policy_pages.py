from django.db import migrations


def create_policy_pages(apps, schema_editor):
    # The text lives in apps/content/policy_pages.py, shared with seed_content.
    # Only pages whose slug is missing are created: an edited policy is never
    # overwritten, and running this again changes nothing.
    from apps.content.policy_pages import create_missing

    Page = apps.get_model("content", "Page")
    PageSection = apps.get_model("content", "PageSection")
    SiteSettings = apps.get_model("content", "SiteSettings")
    settings_row = SiteSettings.objects.first()
    create_missing(Page, PageSection, email=(settings_row.email if settings_row else "") or "")


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0004_sync_public_business_links"),
    ]

    operations = [
        migrations.RunPython(create_policy_pages, migrations.RunPython.noop),
    ]
