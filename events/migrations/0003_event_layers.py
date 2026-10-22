import datetime

from django.db import migrations, models
from django.utils import timezone


def convert(apps, schema_editor):
    """Each event's start/end datetimes become its date(s) and minutes; restaurant_open decides open or closed; the
    old in-person/online/delivery switches become the channels it takes."""
    Event = apps.get_model("events", "Event")
    for ev in Event.objects.all():
        start, end = timezone.localtime(ev.start), timezone.localtime(ev.end)
        ev.kind = "open" if ev.restaurant_open else "closed"
        ev.start_date = start.date()
        minutes = start.hour * 60 + start.minute
        length = max(15, int((end - start).total_seconds() // 60))
        if length >= 24 * 60 and minutes == 0:  # whole days
            ev.end_date = (end - datetime.timedelta(minutes=1)).date()
            ev.all_day, ev.opens, ev.closes = ev.kind == "closed", 0, 1440
        else:
            ev.end_date = ev.start_date
            ev.opens, ev.closes = minutes, min(minutes + length, 1680)
        ev.dine_in, ev.pickup, ev.delivery = ev.in_person_open, ev.online_open, ev.delivery_open
        ev.save()


class Migration(migrations.Migration):
    dependencies = [("events", "0002_store_hours")]

    operations = [
        migrations.AddField("event", "kind", models.CharField(choices=[("open", "Open for an event"), ("closed", "Close the store")], default="open", max_length=6)),
        migrations.AddField("event", "start_date", models.DateField(null=True)),
        migrations.AddField("event", "end_date", models.DateField(null=True)),
        migrations.AddField("event", "all_day", models.BooleanField(default=False, help_text="Closed all day (closures only)")),
        migrations.AddField("event", "opens", models.PositiveSmallIntegerField(default=540)),
        migrations.AddField("event", "closes", models.PositiveSmallIntegerField(default=1260)),
        migrations.AddField("event", "dine_in", models.BooleanField(default=True, help_text="Open for an event: taking dine-in")),
        migrations.AddField("event", "pickup", models.BooleanField(default=True, help_text="Open for an event: taking pickup orders")),
        migrations.AddField("event", "delivery", models.BooleanField(default=False, help_text="Open for an event: taking delivery orders")),
        migrations.AddField("event", "scope", models.CharField(choices=[("all", "Close everything"), ("online", "Stop online orders only"), ("delivery", "Stop delivery only")], default="all", help_text="Closed: what closes", max_length=8)),
        migrations.AddField("event", "banner", models.BooleanField(default=True, help_text="A banner on the online store")),
        migrations.AddField("event", "banner_days", models.PositiveSmallIntegerField(default=7, help_text="Days before it starts that the banner shows")),
        migrations.RunPython(convert, migrations.RunPython.noop),
        migrations.RemoveField("event", "start"),
        migrations.RemoveField("event", "end"),
        migrations.RemoveField("event", "restaurant_open"),
        migrations.RemoveField("event", "in_person_open"),
        migrations.RemoveField("event", "online_open"),
        migrations.RemoveField("event", "delivery_open"),
        migrations.AlterField("event", "start_date", models.DateField()),
        migrations.AlterField("event", "end_date", models.DateField()),
        migrations.AlterField("event", "description", models.TextField(blank=True, help_text="What customers see", null=True)),
        migrations.AlterModelOptions("event", {"ordering": ["start_date", "opens"]}),
    ]
