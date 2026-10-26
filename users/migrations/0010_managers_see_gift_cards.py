from django.db import migrations


def managers_see_giftcards(apps, schema_editor):
    """The Managers group had every admin page (users 0006); the new Gift cards page too."""
    GroupInfo = apps.get_model("users", "GroupInfo")
    for info in GroupInfo.objects.filter(group__name="Managers"):
        info.perms = list(info.perms or []) + [c for c in ("admin.giftcards.view", "admin.giftcards.edit")
                                              if c not in (info.perms or [])]
        info.save(update_fields=["perms"])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_managers_see_discounts'),
    ]

    operations = [
        migrations.RunPython(managers_see_giftcards, migrations.RunPython.noop),
    ]
