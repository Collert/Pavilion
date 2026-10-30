"""Couriers' subscriptions move here from the old deliveries.PushSubscription (deleted next by deliveries 0005)."""
from django.db import migrations


def move(apps, schema_editor):
    Old = apps.get_model("deliveries", "PushSubscription")
    New = apps.get_model("push", "PushSubscription")
    seen = set(New.objects.values_list("endpoint", flat=True))
    for old in Old.objects.order_by("-id"):  # the newest row for an endpoint wins
        if old.endpoint in seen or not old.endpoint.startswith("https://") or len(old.endpoint) > 1024:
            continue
        seen.add(old.endpoint)
        New.objects.create(user_id=old.user_id, app="deliveries", endpoint=old.endpoint, p256dh=old.p256dh[:255], auth=old.auth[:255])


class Migration(migrations.Migration):

    dependencies = [
        ("push", "0001_initial"),
        ("deliveries", "0004_courier_app"),
    ]

    operations = [migrations.RunPython(move, migrations.RunPython.noop)]
