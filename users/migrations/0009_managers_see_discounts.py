from django.db import migrations


def managers_see_discounts(apps, schema_editor):
    """The Managers group had every admin page (users 0006); the new Discounts page too."""
    GroupInfo = apps.get_model("users", "GroupInfo")
    for info in GroupInfo.objects.filter(group__name="Managers"):
        info.perms = list(info.perms or []) + [c for c in ("admin.discounts.view", "admin.discounts.edit", "admin.discounts.delete")
                                              if c not in (info.perms or [])]
        info.save(update_fields=["perms"])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_managers_see_coupons'),
    ]

    operations = [
        migrations.RunPython(managers_see_discounts, migrations.RunPython.noop),
    ]
