"""
Make "one row per viewer per product" actually true.

The old `unique_together = ("user", "session_key", "product")` never enforced
anything, because every row has one of the two owner columns NULL — a guest
has no user, a signed-in customer has no session_key — and Postgres treats
NULL as distinct from NULL in a unique index. Two concurrent views of the
same product page therefore inserted two rows, and `update_or_create`'s
`get()` raised MultipleObjectsReturned on every subsequent view: a permanent
500 on that product page for that visitor, which is how this was found.

Duplicates already exist in any database that has served traffic, so they are
collapsed here before the real constraints go on. The newest row wins, which
is the one `viewed_at` would have kept anyway.
"""
from django.conf import settings
from django.db import migrations, models


def collapse_duplicates(apps, schema_editor):
    RecentlyViewed = apps.get_model("catalog", "RecentlyViewed")

    seen = set()
    doomed = []
    # Newest first, so the row we keep for each owner+product is the latest.
    for row in RecentlyViewed.objects.order_by("-viewed_at", "-id").iterator():
        key = (row.user_id, row.session_key, row.product_id)
        if key in seen:
            doomed.append(row.id)
        else:
            seen.add(key)

    for start in range(0, len(doomed), 500):
        RecentlyViewed.objects.filter(id__in=doomed[start : start + 500]).delete()


def noop(apps, schema_editor):
    """Nothing to undo — the deleted rows were duplicates of surviving ones."""


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0003_alter_productvideo_thumbnail"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="recentlyviewed",
            unique_together=set(),
        ),
        migrations.RunPython(collapse_duplicates, noop),
        migrations.AddConstraint(
            model_name="recentlyviewed",
            constraint=models.UniqueConstraint(
                condition=models.Q(("session_key__isnull", True)),
                fields=("user", "product"),
                name="uniq_recentlyviewed_user_product",
            ),
        ),
        migrations.AddConstraint(
            model_name="recentlyviewed",
            constraint=models.UniqueConstraint(
                condition=models.Q(("user__isnull", True)),
                fields=("session_key", "product"),
                name="uniq_recentlyviewed_session_product",
            ),
        ),
    ]
