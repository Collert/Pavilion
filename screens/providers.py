"""
Data for widgets. A widget's JSON names one of these as "context"; it's called as fn(request, params) and what it
returns is the template's `data`. Keep them cheap (screens refetch on every live nudge) and return only what a
public screen may show.
"""
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from pos_server.models import OrderStation, active_orders_queryset

logger = logging.getLogger(__name__)

ENTERING_SECONDS = 3  # a tile that arrived this recently plays its entrance


def public_name(name):
    """What a public board shows of a customer's name: "Priya Singh" → "Priya S."."""
    words = (name or "").split()
    if not words:
        return None
    shown = words[0] if len(words) == 1 else f"{words[0]} {words[-1][0].upper()}."
    return shown[:24]


def _board_orders(request):
    """Active pickup orders as (order, statuses, ready_at), loaded once per request for every order widget on it."""
    if not hasattr(request, "_screen_orders"):
        orders = (active_orders_queryset().exclude(channel="delivery")  # couriers collect those
                  .prefetch_related("station_statuses").order_by("timestamp"))
        rows = []
        for order in orders:
            stations = list(order.station_statuses.all())
            ready_at = max((row.ready_at for row in stations if row.ready_at), default=None)
            rows.append((order, [row.status for row in stations], ready_at))
        request._screen_orders = rows
    return request._screen_orders


def order_list(request, params):
    """Orders being prepared (oldest first) or ready and waiting to be picked up (newest first)."""
    now = timezone.now()
    working = (OrderStation.STATUS_PENDING, OrderStation.STATUS_APPROVED)
    if params["show"] == "ready":
        picked = [(order, ready_at or order.timestamp) for order, statuses, ready_at in _board_orders(request)
                  if statuses and not order.picked_up and not any(status in working for status in statuses)]
        picked.sort(key=lambda pair: pair[1], reverse=True)
    else:
        picked = [(order, max(order.timestamp, order.start_time)) for order, statuses, _ in _board_orders(request)
                  if any(status in working for status in statuses)]
    limit = params["limit"]
    tiles = []
    for order, since in picked[:limit]:
        age = (now - since).total_seconds()
        fresh = params["show"] == "ready" and age < params["fresh_seconds"]
        tiles.append({
            "number": f"{order.id:04d}",
            "name": public_name(order.name),
            "entering": age < ENTERING_SECONDS,
            "fresh": fresh,
            # When "Just ready" should go: the page refetches then (see data-ends in static/js/live-page.js)
            "fresh_until": int((since.timestamp() + params["fresh_seconds"]) * 1000) if fresh else None,
        })
    return {"tiles": tiles, "count": len(picked), "more": max(0, len(picked) - limit)}


# ---------- weather ----------

WEATHER_URL = "https://api.weatherapi.com/v1/current.json"
WEATHER_TTL = 15 * 60
WEATHER_RETRY = 5 * 60
# weatherapi.com condition codes → the icon we draw (anything else is "cloud")
WEATHER_KINDS = {
    "sun": {1000},
    "partly": {1003},
    "rain": {1063, 1072, 1150, 1153, 1168, 1171, 1180, 1183, 1186, 1189, 1192, 1195, 1198, 1201, 1240, 1243, 1246},
    "snow": {1066, 1069, 1114, 1117, 1204, 1207, 1210, 1213, 1216, 1219, 1222, 1225, 1237, 1249, 1252, 1255, 1258,
             1261, 1264},
    "storm": {1087, 1273, 1276, 1279, 1282},
}


def _fetch_weather(location):
    response = requests.get(WEATHER_URL, params={"key": settings.WEATHER_API_KEY, "q": location, "aqi": "no"}, timeout=4)
    response.raise_for_status()
    current = response.json()["current"]
    code = current["condition"]["code"]
    kind = next((name for name, codes in WEATHER_KINDS.items() if code in codes), "cloud")
    if kind == "sun" and not current.get("is_day", 1):
        kind = "night"
    return {"temp_c": current["temp_c"], "temp_f": current["temp_f"], "condition": current["condition"]["text"],
            "kind": kind}


def weather(request, params):
    """Current weather from weatherapi.com (WEATHER_API_KEY), fetched on the server and cached for 15 minutes."""
    location = params["location"].strip()
    if not settings.WEATHER_API_KEY or not location:
        return {"current": None}
    key = f"screens:weather:{location}"
    entry = cache.get(key)
    if entry is None:
        now = timezone.now().timestamp()
        try:
            entry = {"current": _fetch_weather(location), "until": now + WEATHER_TTL}
        except (requests.RequestException, KeyError, TypeError, ValueError):
            logger.warning("Couldn't fetch the weather for %s", location, exc_info=True)
            entry = {"current": None, "until": now + WEATHER_RETRY}
        cache.set(key, entry, timeout=int(entry["until"] - now))
    current = entry["current"]
    if current:
        current = {**current, "temp": round(current["temp_f"] if params["units"] == "f" else current["temp_c"])}
    # The page refetches when this runs out (see data-ends in static/js/live-page.js), picking up fresh weather
    return {"current": current, "refresh_at": int(entry["until"] * 1000) + 2000}
