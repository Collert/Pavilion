"""
Travel times from the restaurant (its address in Store info) to delivery addresses, from the Google Distance
Matrix API. Each delivery's times are looked up once, for every travel mode, and kept on the Delivery
(`distance_m`, `travel_seconds`). Without a Google Maps key (Store info), or when Google fails, they stay unknown and the
courier app shows "–"; a failed lookup isn't retried for 10 minutes.
"""
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from .models import CourierShift

logger = logging.getLogger(__name__)

URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
MODES = [mode for mode, _label in CourierShift.MODES]
RETRY_AFTER = 600
MAX_DESTINATIONS = 25  # per request


def origin() -> str:
    """Where couriers start: the store's address (Store info)."""
    from admin_portal import store_info
    return store_info.full_address()


def api_key() -> str:
    from admin_portal import store_info
    return store_info.google_key()


def _matrix(destinations, mode):
    """[(seconds, metres) or None per destination]."""
    response = requests.get(URL, timeout=5, params={
        "origins": origin(), "destinations": "|".join(destinations),
        "mode": mode, "key": api_key(),
    })
    response.raise_for_status()
    elements = response.json()["rows"][0]["elements"]
    return [(e["duration"]["value"], e["distance"]["value"]) if e.get("status") == "OK" else None for e in elements]


def fill_travel_times(deliveries):
    """Look up the travel times of the deliveries that don't have them yet (one request per mode)."""
    if not (api_key() and origin()):
        return
    todo = [d for d in deliveries if d.distance_m is None and not cache.get(f"deliveries:no-route:{d.id}")][:MAX_DESTINATIONS]
    if not todo:
        return
    found = {d.id: {} for d in todo}
    distance = {}
    try:
        for mode in MODES:
            for delivery, result in zip(todo, _matrix([d.destination for d in todo], mode)):
                if result:
                    found[delivery.id][mode] = result[0]
                    if mode == CourierShift.DRIVING or delivery.id not in distance:
                        distance[delivery.id] = result[1]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        logger.warning("Distance Matrix lookup failed", exc_info=True)
    for delivery in todo:
        if delivery.id in distance:
            delivery.distance_m = distance[delivery.id]
            delivery.travel_seconds = found[delivery.id]
            delivery.save(update_fields=["distance_m", "travel_seconds"])
        else:
            cache.set(f"deliveries:no-route:{delivery.id}", True, RETRY_AFTER)


def minutes(delivery, mode):
    """Travel time in whole minutes (at least 1) for `mode`, or None if unknown."""
    seconds = (delivery.travel_seconds or {}).get(mode)
    return max(1, round(seconds / 60)) if seconds else None


def maps_link(destination, mode):
    """Google Maps directions to `destination` for `mode` (opens the Maps app on phones)."""
    from urllib.parse import urlencode
    return "https://www.google.com/maps/dir/?" + urlencode({"api": 1, "destination": destination, "travelmode": mode})


def customer_estimate(destination):
    """
    For the online store's checkout: {"min_time", "max_time", "one_time"} over the travel modes couriers on shift
    use (each a Distance Matrix duration {"text", "value"}). None when nobody is on shift.
    """
    modes = set(CourierShift.objects.values_list("mode", flat=True))
    if not modes:
        return None
    times = []
    for mode in modes:
        response = requests.get(URL, timeout=5, params={
            "origins": origin(), "destinations": destination, "mode": mode, "key": api_key()})
        times.append(response.json()["rows"][0]["elements"][0]["duration"])
    low, high = min(times, key=lambda t: t["value"]), max(times, key=lambda t: t["value"])
    return {"min_time": low, "max_time": high, "one_time": low if low["value"] == high["value"] else None}
