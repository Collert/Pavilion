from django.db import migrations


def managers_see_gift_designs(apps, schema_editor):
    """The Managers group had every admin page (users 0006); the new Gift card designs page too."""
    GroupInfo = apps.get_model("users", "GroupInfo")
    for info in GroupInfo.objects.filter(group__name="Managers"):
        info.perms = list(info.perms or []) + [c for c in ("admin.giftdesigns.view", "admin.giftdesigns.edit", "admin.giftdesigns.delete")
                                              if c not in (info.perms or [])]
        info.save(update_fields=["perms"])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0010_managers_see_gift_cards'),
    ]

    operations = [
        migrations.RunPython(managers_see_gift_designs, migrations.RunPython.noop),
    ]
