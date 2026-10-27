"""
How long things usually take, for the store's "ready in about…" and delivery times and the courier app: the median
kitchen time of recent orders (Order.prep_time) and the median drive of recent deliveries (picked up to at the door).
Medians, with anything over two hours left out, so a ticket nobody closed on the KDS doesn't move them. Cached for
10 minutes (forget() drops them).
"""
import datetime
import statistics

from django.core.cache import cache
from django.utils import timezone

PREP_DAYS, DRIVE_DAYS = 14, 30
LONGEST = datetime.timedelta(hours=2)
KEYS = {"prep": "estimates:prep", "drive": "estimates:drive"}


def prep_minutes():
    """Median minutes from an order starting to every station being done, or None without history."""
    return _cached("prep", _prep)


def drive_minutes():
    """Median minutes from a courier picking an order up to reaching the door, or None without history."""
    return _cached("drive", _drive)


def forget():
    cache.delete_many(list(KEYS.values()))


def _cached(name, work):
    hit = cache.get(KEYS[name])
    if hit is None:
        hit = {"v": work()}  # wrapped: "no history" (None) is cached too
        cache.set(KEYS[name], hit, 600)
    return hit["v"]


def _median_minutes(spans):
    minutes = [s.total_seconds() / 60 for s in spans if datetime.timedelta(0) < s <= LONGEST]
    return statistics.median(minutes) if minutes else None


def _prep():
    from .models import Order
    since = timezone.now() - datetime.timedelta(days=PREP_DAYS)
    return _median_minutes(Order.objects.filter(timestamp__gte=since, prep_time__isnull=False).values_list("prep_time", flat=True))


def _drive():
    from deliveries.models import Delivery
    since = timezone.now() - datetime.timedelta(days=DRIVE_DAYS)
    rows = Delivery.objects.filter(picked_up_at__gte=since).exclude(arrived_at=None, delivered_at=None)
    return _median_minutes((arrived or delivered) - picked for picked, arrived, delivered
                           in rows.values_list("picked_up_at", "arrived_at", "delivered_at"))
