"""
Spoken calls on voice channels: "Order for Priya S. is ready" when an order turns ready, and staff announcements.
A screen's order-ready pop-up with "Read it out" on (or its announcement pop-up) and a voice channel picked says where:
the speech is made once (misc_tools/speech.py: gTTS after a chime), saved under files/calls/, and sent to each of those
channels as a clip (audio/voice.py: broadcast); screens on the channel play it, ducking the channels below.

Making speech takes a second or two and needs the internet, so it runs off the request (a thread), never holding up
marking an order ready. Clips older than an hour are deleted when the next one is made.
"""
import datetime
import logging
import threading
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)

CALLS_DIR = "files/calls"
KEEP = datetime.timedelta(hours=1)
background = True  # tests run calls straight away


def channels_for(popup):
    """The store voice channels screens want `popup` ("order_ready" or "announcement") spoken on."""
    from screens.models import Screen
    out = set()
    for screen in Screen.objects.exclude(popups=[]):
        entries = {e.get("id"): e for e in screen.audio or [] if isinstance(e, dict)}
        for spec in screen.popups or []:
            if not isinstance(spec, dict) or spec.get("widget") != popup:
                continue
            params = spec.get("params") or {}
            if popup == "order_ready" and params.get("speak") is False:
                continue
            entry = entries.get(params.get("channel"))
            channel = (entry or {}).get("params", {}).get("channel") if entry and entry.get("widget") == "voice_channel" else None
            if channel:
                out.add(int(channel))
    from .models import AudioChannel
    return set(AudioChannel.objects.filter(id__in=out, kind=AudioChannel.VOICE, archived=False).values_list("id", flat=True))


def _clean():
    try:
        _dirs, files = default_storage.listdir(CALLS_DIR)
    except (FileNotFoundError, OSError):
        return
    cutoff = timezone.now() - KEEP
    for name in files:
        path = f"{CALLS_DIR}/{name}"
        try:
            if default_storage.get_modified_time(path) < cutoff:
                default_storage.delete(path)
        except Exception:
            pass


def _speak(text, popup):
    from misc_tools.speech import speech_mp3

    from pavilion.realtime import publish

    from .voice import broadcast, log
    try:
        channels = channels_for(popup)
        if not channels:
            return
        audio = speech_mp3(text)
        name = default_storage.save(f"{CALLS_DIR}/{uuid.uuid4().hex[:12]}.mp3", ContentFile(audio))
        url = default_storage.url(name)
        for channel in channels:
            broadcast(channel, {"type": "clip", "url": url, "text": text})
            log(channel, {"kind": "call", "text": text, "url": url})  # the Audio app can play it again
        publish("audio")
        _clean()
    except Exception:
        logger.exception("Couldn't speak %r", text)
    finally:
        close_old_connections()


def speak(text, popup):
    """Say `text` on the voice channels screens picked for `popup`."""
    if background:
        threading.Thread(target=_speak, args=(text, popup), daemon=True).start()
    else:
        _speak(text, popup)


def order_ready(name, number):
    who = name or (f"number {int(number)}" if str(number).isdigit() else str(number))
    speak(f"Order for {who} is ready.", "order_ready")
