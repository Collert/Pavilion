"""
Music channels are radio stations. Each channel keeps a queue of songs (started from a playlist or a song in the
Audio app, added to with "Play next" and "Add to queue"), which entry is playing and when it started, how it repeats
(off: it stops after the last song; all: the queue starts again; one: the song does) and its volume. Every screen on
the channel asks now_playing() and plays that song's file straight from the media storage, seeked to the same moment,
so they all stay in step; nothing streams through Django. Controls (control()) move the timeline and nudge screens
and the Audio app over SSE ("audio").

Time only moves the timeline when someone asks: now_playing() walks forward past songs that have ended and saves
where it got to.

    queue = [{"k": 3, "t": track id}, ...]   (k: the entry's key, so the same song can be queued twice)
"""
import datetime
import random

from django.db import transaction
from django.utils import timezone

from pavilion.realtime import publish

from .models import AudioChannel, Track

UNKNOWN_LENGTH = 180  # seconds, for a song whose length wasn't read
RESTART_AFTER = 3  # seconds: "previous" past this restarts the song


def _length(track):
    return track.duration if track and track.duration > 1 else UNKNOWN_LENGTH


def _save(channel, fields):
    """Write only what moved, as one short UPDATE (no lock: screens asking at once work out the same answer)."""
    if fields:
        AudioChannel.objects.filter(pk=channel.pk).update(**fields)


def now_playing(channel, now=None) -> dict:
    """Where the channel is: {"track", "offset" (seconds into it), "paused", "stopped" (paused at the end of the
    queue), "started_at", "ends_at", "next" (Track or None), "cur", "entries": [(entry, Track)], "tracks": {id: Track}}.
    Saves how far time has moved it, and drops songs that were deleted from the library."""
    now = now or timezone.now()
    channel = AudioChannel.objects.get(pk=channel.pk)
    queue = [e for e in channel.queue if isinstance(e, dict)]
    tracks = Track.objects.in_bulk([e.get("t") for e in queue])
    fields = {}
    cur = channel.cur
    started_at, paused_at = channel.started_at, channel.paused_at
    kept = [e for e in queue if e.get("t") in tracks]
    if len(kept) != len(queue):  # songs deleted: the queue closes up around them
        playing = queue[cur] if cur < len(queue) else None
        if playing in kept:
            cur = kept.index(playing)
        else:
            cur = sum(1 for e in queue[:cur] if e in kept)
            started_at = now if paused_at is None else paused_at  # the song that was playing is gone: the next one starts
        queue = kept
        fields.update(queue=queue, cur=cur, started_at=started_at)
    if not queue:
        _save(channel, fields)
        return {"track": None, "offset": 0, "paused": True, "stopped": True, "next": None, "cur": 0, "entries": [], "tracks": tracks,
                "started_at": None, "ends_at": None, "channel": channel}
    if cur >= len(queue):
        cur, started_at = len(queue) - 1, None
        fields["cur"] = cur
    if started_at is None:
        started_at = paused_at or now
        fields["started_at"] = started_at
    length = lambda i: _length(tracks[queue[i]["t"]])  # noqa: E731
    clock = paused_at or now
    elapsed = (clock - started_at).total_seconds()
    if elapsed >= length(cur) and paused_at is None:
        if channel.repeat == AudioChannel.REPEAT_ONE:
            rounds = int(elapsed // length(cur))
            started_at += datetime.timedelta(seconds=rounds * length(cur))
            elapsed -= rounds * length(cur)
        else:
            total = sum(length(i) for i in range(len(queue)))
            laps = 0
            while elapsed >= length(cur) and laps < 5000:
                laps += 1
                if channel.repeat == AudioChannel.REPEAT_ALL and cur == 0 and elapsed >= total:  # skip whole rounds
                    rounds = int(elapsed // total)
                    started_at += datetime.timedelta(seconds=rounds * total)
                    elapsed -= rounds * total
                    continue
                if cur + 1 < len(queue) or channel.repeat == AudioChannel.REPEAT_ALL:
                    elapsed -= length(cur)
                    started_at += datetime.timedelta(seconds=length(cur))
                    cur = (cur + 1) % len(queue)
                else:  # the end of the queue: stop on the last song, back at its start
                    started_at = paused_at = now
                    elapsed = 0
                    fields["paused_at"] = paused_at
                    break
        fields.update(cur=cur, started_at=started_at)
    _save(channel, fields)
    channel.queue, channel.cur, channel.started_at, channel.paused_at = queue, cur, started_at, paused_at
    track = tracks[queue[cur]["t"]]
    if cur + 1 < len(queue):
        upcoming = tracks[queue[cur + 1]["t"]]
    elif channel.repeat == AudioChannel.REPEAT_ALL:
        upcoming = tracks[queue[0]["t"]]
    else:
        upcoming = None
    if channel.repeat == AudioChannel.REPEAT_ONE:
        upcoming = track
    paused = paused_at is not None
    return {"track": track, "offset": max(elapsed, 0.0), "paused": paused,
            "stopped": paused and cur == len(queue) - 1 and elapsed == 0 and channel.repeat == AudioChannel.REPEAT_OFF,
            "started_at": started_at, "ends_at": started_at + datetime.timedelta(seconds=length(cur)), "next": upcoming,
            "cur": cur, "entries": [(e, tracks[e["t"]]) for e in queue], "tracks": tracks, "channel": channel}


def song_json(t):
    if t is None:
        return None
    return {"id": t.id, "title": t.title, "artist": t.artist, "album": t.album, "url": t.file.url, "duration": _length(t),
            "artwork": t.artwork.url if t.artwork else ""}


def as_json(channel, request=None) -> dict:
    """What a screen needs to play the channel (times in epoch ms, so it can line up with the server's clock)."""
    state = now_playing(channel)
    track = state["track"]
    now = timezone.now()
    return {"channel": channel.id, "kind": channel.kind, "name": channel.name, "paused": state["paused"],
            "volume": state["channel"].volume, "track": song_json(track), "next": song_json(state["next"]),
            "offset": round(state["offset"], 3), "server_ms": int(now.timestamp() * 1000),
            "ends_ms": int(state["ends_at"].timestamp() * 1000) if track and not state["paused"] else None}


# ---------- controls ----------

OPS = {"play", "pause", "toggle", "next", "prev", "seek", "jump", "shuffle", "repeat", "volume",
       "playnow", "playnext", "playlast", "tonext", "remove", "move", "clear"}


def _keyed(channel, ids):
    start = max((e.get("k", 0) for e in channel.queue), default=0) + 1
    return [{"k": start + n, "t": t} for n, t in enumerate(ids)]


def _start(channel, cur, now, playing=None):
    """Put entry `cur` on from its start, playing or not (by default as it was)."""
    channel.cur = cur
    channel.started_at = now
    keep_paused = channel.paused_at is not None if playing is None else not playing
    channel.paused_at = now if keep_paused else None


def control(channel, op, *, ids=(), key=None, value=None, source="", now=None) -> str:
    """Do `op` on a music channel. Returns a short message for staff ("" when there's nothing to say).

    play | pause | toggle | next | prev | seek (value: seconds) | jump (key: play that entry now) | shuffle (on: the
    songs after this one are shuffled) | repeat (value: off/all/one, or cycle) | volume (value: 0-100) |
    playnow (ids, source: replaces the queue) | playnext / playlast (ids) | tonext (key) | remove (key) |
    move (key, value: the index it goes to) | clear (only the current song stays)."""
    now = now or timezone.now()
    now_playing(channel, now)  # bring the timeline up to now first
    with transaction.atomic():
        channel = AudioChannel.objects.select_for_update().get(pk=channel.pk)
        queue = list(channel.queue)
        cur = min(channel.cur, max(len(queue) - 1, 0))
        keys = [e.get("k") for e in queue]
        at = keys.index(key) if key in keys else None
        clock = channel.paused_at or now
        offset = (clock - channel.started_at).total_seconds() if channel.started_at else 0
        known = set(Track.objects.filter(pk__in=ids).values_list("id", flat=True)) if ids else set()
        ids = [i for i in ids if i in known]
        msg = ""
        if op in ("play", "toggle") and channel.paused_at and queue:
            channel.started_at = (channel.started_at or now) + (now - channel.paused_at)  # resume where it stopped
            channel.paused_at = None
        elif op in ("pause", "toggle") and not channel.paused_at and queue:
            channel.paused_at = now
        elif op == "next" and queue:
            if cur + 1 < len(queue):
                _start(channel, cur + 1, now)
            elif channel.repeat == AudioChannel.REPEAT_ALL:
                _start(channel, 0, now)
            else:
                _start(channel, cur, now, playing=False)  # past the end: stop
        elif op == "prev" and queue:
            _start(channel, cur if offset > RESTART_AFTER or cur == 0 else cur - 1, now)
        elif op == "seek" and queue and value is not None:
            track = Track.objects.filter(pk=queue[cur]["t"]).first()
            pos = min(max(float(value), 0), max(_length(track) - 0.5, 0))
            channel.started_at = clock - datetime.timedelta(seconds=pos)
        elif op == "jump" and at is not None:
            _start(channel, at, now, playing=True)
        elif op == "shuffle":
            channel.shuffle = not channel.shuffle if value is None else bool(value)
            if channel.shuffle:
                rest = queue[cur + 1:]
                random.shuffle(rest)
                queue = queue[:cur + 1] + rest
                msg = "shuffled"
        elif op == "repeat":
            order = [AudioChannel.REPEAT_OFF, AudioChannel.REPEAT_ALL, AudioChannel.REPEAT_ONE]
            channel.repeat = value if value in order else order[(order.index(channel.repeat) + 1) % 3]
        elif op == "volume" and value is not None:
            channel.volume = min(max(int(value), 0), 100)
        elif op == "playnow" and ids:
            queue = _keyed(channel, ids)
            channel.source = source[:120]
            _start(channel, 0, now, playing=True)
        elif op == "playnext" and ids:
            queue[cur + 1:cur + 1] = _keyed(channel, ids)
            if len(queue) == len(ids):
                _start(channel, 0, now, playing=True)
        elif op == "playlast" and ids:
            queue += _keyed(channel, ids)
            if len(queue) == len(ids):
                _start(channel, 0, now, playing=True)
        elif op == "tonext" and at is not None and at not in (cur, cur + 1):
            entry = queue.pop(at)
            if at < cur:
                cur -= 1
            queue.insert(cur + 1, entry)
            channel.cur = cur
        elif op == "remove" and at is not None and at != cur:
            queue.pop(at)
            if at < cur:
                channel.cur = cur - 1
        elif op == "move" and at is not None and value is not None:
            playing_key = queue[cur]["k"]
            entry = queue.pop(at)
            to = min(max(int(value), 0), len(queue))
            queue.insert(to, entry)
            channel.cur = [e["k"] for e in queue].index(playing_key)
        elif op == "clear" and len(queue) > 1:
            queue = [queue[cur]]
            channel.cur = 0
        channel.queue = queue
        channel.save()
    publish("audio")
    return msg
