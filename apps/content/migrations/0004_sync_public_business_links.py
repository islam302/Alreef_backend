from django.db import migrations


TIKTOK_URL = "https://www.tiktok.com/@alreeforhony"
MAPS_URL = (
    "https://maps.google.com/?q=V35C%2BRPF+%D8%B4%D8%A8%D8%B4%D9%8A%D8%B1"
    "+%D8%A7%D9%84%D8%AD%D8%B5%D8%A9+%D8%B7%D9%86%D8%B7%D8%A7"
)


def sync_public_business_links(apps, schema_editor):
    Branch = apps.get_model("content", "Branch")
    SocialLink = apps.get_model("content", "SocialLink")

    Branch.objects.filter(plus_code="V35C+RPF").update(maps_url=MAPS_URL)

    # Migrations update an already configured store, while `seed_content`
    # remains responsible for populating a genuinely empty installation.
    if SocialLink.objects.exists():
        SocialLink.objects.update_or_create(
            url=TIKTOK_URL,
            defaults={
                "platform": "tiktok",
                "label": "تيك توك",
                "sort_order": 2,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0003_update_verified_contact_details"),
    ]

    operations = [
        migrations.RunPython(sync_public_business_links, migrations.RunPython.noop),
    ]
