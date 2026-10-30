"""
Screenshots of the online store for the installed app's manifest: Chrome shows them in its richer install sheet
(online_store/app.py lists them). They're taken by a Gotenberg server (headless Chromium; docker-compose.yml runs
one as `screenshots`) opening the store the way a guest sees it, at a phone's size (the home page and the menu) and a
computer's (the home page).

Publishing on the admin's Online store page retakes them, after the save commits and in the background, so Publish
never waits on a browser. One run at a time (a cache lock); a Publish during a run asks for one more run after it.
Each run saves new files (store_app/<form>-<n>-<time>.png in the media storage) and only then deletes the last run's,
so the bucket holds one set and browsers never get a cached old picture under a new name. StoreSettings.app_screenshots
lists the current set. Without SCREENSHOT_URL (development) nothing is taken and the manifest has no screenshots.
"""
import logging
import time
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.urls import reverse

from push.send import background

logger = logging.getLogger(__name__)
LOCK, AGAIN = "store-screenshots:running", "store-screenshots:again"
# (form factor, page url name, viewport width, height): Chrome wants narrow (phones) and wide (computers) ones
SHOTS = [("narrow", "store_home", 412, 915), ("narrow", "menu", 412, 915), ("wide", "store_home", 1280, 800)]


def configured() -> bool:
    return bool(getattr(settings, "SCREENSHOT_URL", ""))


def take(url, width, height) -> bytes:
    """A PNG of `url` at that viewport, from Gotenberg (it only takes multipart forms: `files` makes requests send one)."""
    fields = {"url": url, "width": str(width), "height": str(height), "format": "png", "waitDelay": "2s",
              "emulatedMediaType": "screen"}
    response = requests.post(urljoin(settings.SCREENSHOT_URL.rstrip("/") + "/", "forms/chromium/screenshot/url"),
                             files={name: (None, value) for name, value in fields.items()}, timeout=90)
    response.raise_for_status()
    if not response.content.startswith(b"\x89PNG"):
        raise ValueError("The screenshot server didn't answer with a picture")
    return response.content


def refresh():
    """Take a new set and replace the old one. Returns the new list (or the old one if anything failed)."""
    from admin_portal.models import StoreSettings
    site = settings.SCREENSHOT_SITE.rstrip("/")
    stamp = int(time.time())
    saved = []
    try:
        for n, (form, page, width, height) in enumerate(SHOTS):
            png = take(site + reverse(page), width, height)
            name = default_storage.save(f"store_app/{form}-{n}-{stamp}.png", ContentFile(png))
            saved.append({"name": name, "form": form, "width": width, "height": height, "page": page})
    except Exception:
        logger.warning("Couldn't take the store app's screenshots", exc_info=True)
        for shot in saved:
            default_storage.delete(shot["name"])
        return StoreSettings.load().app_screenshots
    store = StoreSettings.load()
    old = [s.get("name") for s in (store.app_screenshots or []) if isinstance(s, dict)]
    store.app_screenshots = saved
    store.save(update_fields=["app_screenshots"])
    for name in old:
        if name and name.startswith("store_app/") and name not in {s["name"] for s in saved}:
            try:
                default_storage.delete(name)
            except Exception:
                logger.warning("Couldn't delete the old screenshot %s", name, exc_info=True)
    return saved


def _run():
    """Refresh until nobody asked for another run while one was going."""
    try:
        while True:
            cache.delete(AGAIN)
            refresh()
            if not cache.get(AGAIN):
                break
    finally:
        cache.delete(LOCK)


def refresh_later():
    """After this transaction, in the background: new screenshots (or one more run after the one going now)."""
    if not configured():
        return

    def start():
        if cache.add(LOCK, True, timeout=15 * 60):
            background(_run)
        else:
            cache.set(AGAIN, True, timeout=15 * 60)

    transaction.on_commit(start)
