from django.db import migrations, models


def convert(apps, schema_editor):
    """Closures name the channels they close (what `scope` meant), and special-hours dates become closures: closed
    all day, or closed outside their hours."""
    Event = apps.get_model("events", "Event")
    SpecialHours = apps.get_model("events", "SpecialHours")
    for ev in Event.objects.filter(kind="closed"):
        ev.dine_in = ev.scope == "all"
        ev.pickup = ev.scope in ("all", "online")
        ev.delivery = True
        ev.save(update_fields=["dine_in", "pickup", "delivery"])
    for sp in SpecialHours.objects.all():
        common = {"kind": "closed", "title": sp.note or ("Closed" if sp.closed else "Special hours"), "description": sp.note,
                  "start_date": sp.date, "end_date": sp.date, "dine_in": True, "pickup": True, "delivery": True, "banner": bool(sp.note)}
        if sp.closed:
            Event.objects.create(all_day=True, opens=0, closes=1440, **common)
            continue
        if sp.opens > 0:
            Event.objects.create(opens=0, closes=sp.opens, **{**common, "banner": False})
        if sp.closes < 1680:
            Event.objects.create(opens=sp.closes, closes=1680, **common)


class Migration(migrations.Migration):
    dependencies = [("events", "0003_event_layers")]

    operations = [
        migrations.RunPython(convert, migrations.RunPython.noop),
        migrations.RemoveField("event", "scope"),
        migrations.DeleteModel("SpecialHours"),
        migrations.AlterField(
            model_name='event',
            name='delivery',
            field=models.BooleanField(default=False, help_text='Delivery orders open (or close)'),
        ),
        migrations.AlterField(
            model_name='event',
            name='dine_in',
            field=models.BooleanField(default=True, help_text='Dine-in opens (or closes)'),
        ),
        migrations.AlterField(
            model_name='event',
            name='pickup',
            field=models.BooleanField(default=True, help_text='Pickup orders open (or close)'),
        ),
    ]
