"""
Travel times from the restaurant to delivery addresses, from open-source services on OpenStreetMap data instead of a
paid API:

- **Addresses to coordinates** (geocoding) from a Nominatim server (`/search`). Each address is looked up once and
  the answer cached for 30 days ("not found" for a day). OpenStreetMap's public server
  (https://nominatim.openstreetmap.org) allows at most one request a second and asks to be named and cached
  (https://operations.osmfoundation.org/policies/nominatim/): Pavilion sends its name and the store's email, keeps to
  one lookup a second across every worker and only a few per page, and never looks anything up as people type.
  A self-hosted Nominatim (docker-compose.yml's nominatim service) has no such limit.
- **Times and distances** from a Valhalla server's matrix (`/sources_to_targets`), one request per travel mode
  for all the deliveries waiting (docker-compose.yml runs one).

Both addresses are on the admin's Store info page (VALHALLA_URL / GEOCODER_URL fill in). Each delivery's times are
kept on it (`distance_m`, `travel_seconds`), so it's looked up once. Without the services, or when they fail, times
stay unknown and the courier app shows "–"; a failed lookup isn't retried for 10 minutes.
"""
import hashlib
import logging
import time
from urllib.parse import urlparse

import requests
from django.core.cache import cache

from .models import CourierShift

logger = logging.getLogger(__name__)

MODES = [mode for mode, _label in CourierShift.MODES]
COSTING = {CourierShift.WALKING: "pedestrian", CourierShift.BICYCLING: "bicycle", CourierShift.DRIVING: "auto"}  # Valhalla's
RETRY_AFTER = 600
MAX_DESTINATIONS = 25  # per lookup
FOUND_FOR, NOT_FOUND_FOR = 30 * 86400, 86400
PUBLIC_LOOKUPS = 3  # addresses one page may look up on a rate-limited public server (each waits its second)


class Busy(Exception):
    """The public geocoder's one-a-second turn is taken: try again on the next page."""


def origin() -> str:
    """Where couriers start: the store's address (Store info)."""
    from admin_portal import store_info
    return store_info.full_address()


def _services():
    from admin_portal import store_info
    return store_info.routing_url(), store_info.geocoder_url()


def _public(url) -> bool:
    return (urlparse(url).hostname or "").endswith("openstreetmap.org")


def geocode(address, url=None):
    """(lat, lon) of an address, or None if it can't be found. Raises Busy when a public server's turn is taken."""
    url = url if url is not None else _services()[1]
    address = " ".join((address or "").split())
    if not (url and address):
        return None
    key = "deliveries:geocode:" + hashlib.sha256(address.lower().encode()).hexdigest()[:32]
    known = cache.get(key)
    if known is not None:
        return tuple(known) if known else None
    if _public(url) and not cache.add("deliveries:geocode-turn", 1, timeout=1):  # one a second, every worker together
        time.sleep(1.05)
        if not cache.add("deliveries:geocode-turn", 1, timeout=1):
            raise Busy
    from admin_portal import store_info
    params = {"q": address, "format": "jsonv2", "limit": 1}
    if store_info.contact_email():
        params["email"] = store_info.contact_email()  # Nominatim's policy: say who's asking
    response = requests.get(url.rstrip("/") + "/search", params=params, timeout=5,
                            headers={"User-Agent": "Pavilion restaurant app (delivery travel times)"})
    response.raise_for_status()
    results = response.json()
    found = [float(results[0]["lat"]), float(results[0]["lon"])] if results else []
    cache.set(key, found, FOUND_FOR if found else NOT_FOUND_FOR)
    return tuple(found) if found else None


def _matrix(url, start, points, mode):
    """[(seconds, metres) or None per point] from Valhalla's matrix."""
    response = requests.post(url.rstrip("/") + "/sources_to_targets", timeout=10, json={
        "sources": [{"lat": start[0], "lon": start[1]}],
        "targets": [{"lat": lat, "lon": lon} for lat, lon in points],
        "costing": COSTING[mode], "units": "kilometers", "verbose": False,
    })
    response.raise_for_status()
    table = response.json()["sources_to_targets"]
    if isinstance(table, dict):  # concise: {"durations": [[s, ...]], "distances": [[km, ...]]}
        pairs = zip(table["durations"][0], table["distances"][0])
    else:  # verbose (older servers): [[{"time": s, "distance": km}, ...]]
        pairs = ((cell.get("time"), cell.get("distance")) for cell in table[0])
    return [(round(s), round(km * 1000)) if s is not None and km is not None else None for s, km in pairs]


def fill_travel_times(deliveries):
    """Look up the travel times of the deliveries that don't have them yet (one matrix request per mode)."""
    routing, geocoder = _services()
    if not (routing and geocoder and origin()):
        return
    todo = [d for d in deliveries if d.distance_m is None and not cache.get(f"deliveries:no-route:{d.id}")][:MAX_DESTINATIONS]
    if not todo:
        return
    lookups = PUBLIC_LOOKUPS if _public(geocoder) else len(todo)
    points, failed = {}, []
    try:
        start = geocode(origin(), geocoder)
        if start is None:
            logger.warning("The store's address couldn't be found on the map: %s", origin())
            return
        for delivery in todo:
            if lookups <= 0:
                break  # the rest on the next page
            lookups -= 1
            point = geocode(delivery.destination, geocoder)
            if point:
                points[delivery.id] = point
            else:
                failed.append(delivery)
    except Busy:
        pass
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        logger.warning("Looking up delivery addresses failed", exc_info=True)
        failed = [d for d in todo if d.id not in points]
    found = {d_id: {} for d_id in points}
    distance = {}
    ready = [d for d in todo if d.id in points]
    try:
        for mode in MODES if ready else []:
            for delivery, result in zip(ready, _matrix(routing, start, [points[d.id] for d in ready], mode)):
                if result:
                    found[delivery.id][mode] = result[0]
                    if mode == CourierShift.DRIVING or delivery.id not in distance:
                        distance[delivery.id] = result[1]
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        logger.warning("Valhalla matrix lookup failed", exc_info=True)
    for delivery in ready:
        if delivery.id in distance:
            delivery.distance_m = distance[delivery.id]
            delivery.travel_seconds = found[delivery.id]
            delivery.save(update_fields=["distance_m", "travel_seconds"])
        else:
            failed.append(delivery)
    for delivery in failed:
        cache.set(f"deliveries:no-route:{delivery.id}", True, RETRY_AFTER)


def minutes(delivery, mode):
    """Travel time in whole minutes (at least 1) for `mode`, or None if unknown."""
    seconds = (delivery.travel_seconds or {}).get(mode)
    return max(1, round(seconds / 60)) if seconds else None


def maps_link(destination, mode):
    """Directions to `destination` for `mode` in Google Maps (a plain link, no API: it opens the Maps app on phones)."""
    from urllib.parse import urlencode
    return "https://www.google.com/maps/dir/?" + urlencode({"api": 1, "destination": destination, "travelmode": mode})
