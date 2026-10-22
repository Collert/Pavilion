"""
Music channels are radio stations. The channel keeps its play order (its playlist, or the whole library when it has
none; shuffled or not), which song is playing and when it started. Every screen on the channel asks now_playing()
and plays that song's file straight from the media storage, seeked to the same moment, so they all stay in step;
nothing streams through Django. Controls (play, pause, next, previous, a new playlist) move the timeline and nudge
screens over SSE ("audio").

Time only moves the timeline when someone asks: now_playing() walks forward past songs that have ended (looping the
order) and saves where it got to.
"""
import datetime
import random

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from pavilion.realtime import publish

from .models import AudioChannel, Track

UNKNOWN_LENGTH = 180  # seconds, for a song whose length couldn't be read


def _length(track):
    return track.duration if track and track.duration > 1 else UNKNOWN_LENGTH


def _pool(channel):
    if channel.playlist_id:
        return [t.id for t in channel.playlist.ordered_tracks()]
    return list(Track.objects.order_by("title", "id").values_list("id", flat=True))


def _fresh_order(channel):
    ids = _pool(channel)
    if channel.shuffle:
        random.shuffle(ids)
    return ids


def _maybe_scan():
    """In development, pick up songs dropped into files/music/ without running the scan by hand (every 30 s at most)."""
    from django.conf import settings
    if settings.DEBUG and cache.add("audio:dev-scan", 1, 30):
        from .library import scan
        scan()


def now_playing(channel, now=None) -> dict:
    """{"track": Track or None, "offset": seconds into it, "paused": bool, "started_at", "ends_at", "next": Track}.
    Starts the channel if it never has, and saves how far time has moved it (only when it moved)."""
    _maybe_scan()
    now = now or timezone.now()
    channel = AudioChannel.objects.get(pk=channel.pk)
    tracks = {t.id: t for t in Track.objects.filter(id__in=_pool(channel))}
    order = [i for i in channel.order if i in tracks]
    fields = {}
    if set(order) != set(tracks):  # songs added or removed: a new order
        order = _fresh_order(channel)
        channel.index, channel.started_at = 0, None
        fields.update(index=0, started_at=None)
    if order != channel.order:
        fields["order"] = order
    channel.order = order
    if not order:
        _save(channel, fields)
        return {"track": None, "offset": 0, "paused": bool(channel.paused_at), "next": None}
    if channel.index >= len(order):
        channel.index, channel.started_at = 0, None
    if channel.started_at is None:
        channel.started_at = now
    clock = channel.paused_at or now
    elapsed = (clock - channel.started_at).total_seconds()
    laps = 0
    while elapsed >= _length(tracks[order[channel.index]]) and laps < 10000:
        elapsed -= _length(tracks[order[channel.index]])
        channel.started_at += datetime.timedelta(seconds=_length(tracks[order[channel.index]]))
        channel.index = (channel.index + 1) % len(order)
        laps += 1
    if laps or "started_at" in fields or channel.started_at != AudioChannel.objects.values_list("started_at", flat=True).get(pk=channel.pk):
        fields.update(index=channel.index, started_at=channel.started_at)
    _save(channel, fields)
    track = tracks[order[channel.index]]
    upcoming = tracks[order[(channel.index + 1) % len(order)]]
    return {"track": track, "offset": max(elapsed, 0.0), "paused": bool(channel.paused_at), "started_at": channel.started_at,
            "ends_at": channel.started_at + datetime.timedelta(seconds=_length(track)), "next": upcoming}


def _save(channel, fields):
    """Write only what moved, as one short UPDATE (no lock: screens asking at once work out the same answer)."""
    if fields:
        AudioChannel.objects.filter(pk=channel.pk).update(**fields)


def as_json(channel, request=None) -> dict:
    """What a screen needs to play the channel (times in epoch ms, so it can line up with the server's clock)."""
    state = now_playing(channel)
    track = state["track"]

    def song(t):
        if t is None:
            return None
        return {"id": t.id, "title": t.title, "artist": t.artist, "album": t.album, "url": t.file.url, "duration": _length(t),
                "artwork": t.artwork.url if t.artwork else ""}
    now = timezone.now()
    return {"channel": channel.id, "kind": channel.kind, "name": channel.name, "paused": state["paused"],
            "track": song(track), "next": song(state["next"]), "offset": round(state["offset"], 3),
            "server_ms": int(now.timestamp() * 1000),
            "ends_ms": int(state["ends_at"].timestamp() * 1000) if track and not state["paused"] else None}


def _nudge():
    publish("audio")


def control(channel, action, playlist=None, shuffle=None) -> AudioChannel:
    """play | pause | next | previous | playlist (with `playlist`, a Playlist or None for the whole library)."""
    now = timezone.now()
    now_playing(channel, now)  # bring the timeline up to now first
    with transaction.atomic():
        channel = AudioChannel.objects.select_for_update().get(pk=channel.pk)
        if action == "pause" and not channel.paused_at:
            channel.paused_at = now
        elif action == "play" and channel.paused_at:
            if channel.started_at:
                channel.started_at += now - channel.paused_at  # resume where it stopped
            channel.paused_at = None
        elif action in ("next", "previous") and channel.order:
            step = 1 if action == "next" else -1
            channel.index = (channel.index + step) % len(channel.order)
            channel.started_at = now
            if channel.paused_at:
                channel.paused_at = now
        elif action == "playlist":
            channel.playlist = playlist
            if shuffle is not None:
                channel.shuffle = shuffle
            channel.order = _fresh_order(channel)
            channel.index, channel.started_at = 0, now
            if channel.paused_at:
                channel.paused_at = now
        channel.save()
    _nudge()
    return channel
