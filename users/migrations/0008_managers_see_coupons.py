from django.db import migrations


def managers_see_coupons(apps, schema_editor):
    """The Managers group had every admin page (users 0006); the new Coupons page too."""
    GroupInfo = apps.get_model("users", "GroupInfo")
    for info in GroupInfo.objects.filter(group__name="Managers"):
        info.perms = list(info.perms or []) + [c for c in ("admin.coupons.view", "admin.coupons.edit", "admin.coupons.delete")
                                              if c not in (info.perms or [])]
        info.save(update_fields=["perms"])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0007_customer_admin'),
    ]

    operations = [
        migrations.RunPython(managers_see_coupons, migrations.RunPython.noop),
    ]
