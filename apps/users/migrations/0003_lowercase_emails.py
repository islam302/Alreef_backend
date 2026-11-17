"""
Lower-case every stored email.

Accounts created through social sign-in kept the provider's capitalisation
("Ahmed.Ali@gmail.com"), while password sign-in lowers what is typed, so those
customers could not sign in with a password. Addresses that would collide once
lower-cased (two accounts differing only in case) are left untouched and
reported: merging accounts is a person's decision, not a migration's. For the
same reason no case-insensitive unique constraint is added here — it would fail
on exactly those duplicates; registration checks case-insensitively instead.
"""
import logging

from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower

logger = logging.getLogger(__name__)


def lowercase_emails(apps, schema_editor):
    User = apps.get_model("users", "User")
    duplicated = set(
        User.objects.annotate(lowered=Lower("email"))
        .values("lowered")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("lowered", flat=True)
    )
    for lowered in sorted(duplicated):
        ids = list(User.objects.filter(email__iexact=lowered).values_list("id", flat=True))
        logger.warning("users %s share the email %s apart from case; left as they are", ids, lowered)
        print(f"\n  [users.0003] accounts {ids} differ only by email case ({lowered}); not changed")

    for user in User.objects.exclude(email=Lower("email")).only("id", "email"):
        lowered = user.email.lower()
        if lowered in duplicated:
            continue
        User.objects.filter(pk=user.pk).update(email=lowered)


class Migration(migrations.Migration):
    dependencies = [("users", "0002_alter_user_profile_image")]

    operations = [migrations.RunPython(lowercase_emails, migrations.RunPython.noop)]
