"""
What the Audio app's pages show (designs: AudioRail, AudioChannels, AudioMusic, AudioVoice; views in views.py).
Staff pick a channel on the rail: a music channel's page plays, pauses, skips and queues songs from the Library; a
voice channel's page is push-to-talk (audio/static/audio/voice.js) with the last hour's messages.
"""
import time
import zlib
from urllib.parse import urlencode

from django.utils.translation import gettext as _, ngettext

from . import music, voice
from .models import AudioChannel, Playlist, PlaylistTrack, Track

MUSIC_ICON = "M9 18V5l11-2v13 M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3z M20 16a3 3 0 1 1-3-3 3 3 0 0 1 3 3z"
MIC_ICON = "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z M5 11a7 7 0 0 0 14 0 M12 18v3"
FACES = ["#C9821A", "#4E79B5", "#1F4A3A", "#7A5AC8", "#C2453B", "#2BA3A0", "#12875A"]


def face(name) -> str:
    """A steady colour for someone's initials."""
    return FACES[zlib.crc32((name or "").encode()) % len(FACES)]


def clock(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def mins(seconds) -> str:
    m = round((seconds or 0) / 60)
    return _("%(h)d h %(m)d min") % {"h": m // 60, "m": m % 60} if m >= 60 else _("%(m)d min") % {"m": m}


def ago(at) -> str:
    seconds = max(0, time.time() - at)
    if seconds < 60:
        return _("Just now")
    return ngettext("%(n)d min ago", "%(n)d min ago", int(seconds // 60)) % {"n": int(seconds // 60)}


def songs(n) -> str:
    return ngettext("%(n)d song", "%(n)d songs", n) % {"n": n}


def art(track) -> str:
    return track.artwork.url if track and track.artwork else ""


def ms(dt) -> int:
    return int(dt.timestamp() * 1000) if dt else 0


# ---------- channels ----------

def music_status(channel) -> dict:
    """A music channel at a glance: what's on, playing or not, how far through."""
    state = music.now_playing(channel)
    track = state["track"]
    playing = bool(track) and not state["paused"]
    return {"track": track, "playing": playing, "empty": track is None, "state": state, "art": art(track),
            "pill": _("Idle") if track is None else _("Playing") if playing else _("Paused"),
            "tone": "idle" if track is None else "on" if playing else "paused",
            "sub": _("Nothing queued") if track is None else (f"{track.title} · {track.artist}" if track.artist else track.title) if playing else _("Paused"),
            "title": track.title if track else _("Nothing queued"),
            "artist": " · ".join(x for x in (track.artist, _("from %(source)s") % {"source": state["channel"].source} if state["channel"].source else "") if x)
            if track else _("Open it to pick a playlist"),
            "ends_ms": ms(state["ends_at"]) if playing else 0, "total_ms": int(music._length(track) * 1000) if track else 0,
            "pct": round(state["offset"] / music._length(track) * 100, 2) if track else 0}


def voice_status(channel) -> dict:
    who = voice.talking(channel.id)
    log = voice.recent(channel.id)
    last = log[0] if log else None
    if last and last["kind"] == "voice":
        last_text = _("%(who)s · %(ago)s") % {"who": last["first"], "ago": ago(last["at"])}
    elif last:
        last_text = _("Order call · %(ago)s") % {"ago": ago(last["at"])}
    else:
        last_text = ""
    return {"who": who, "live": bool(who), "face": face(who["name"]) if who else "", "log": log,
            "pill": _("Live") if who else _("Quiet"), "sub": _("%(who)s is talking") % {"who": who["first"]} if who else _("Quiet"),
            "head": _("%(who)s is talking") % {"who": who["name"]} if who else _("Channel is clear"),
            "text": _("Open to listen or reply") if who else (_("Last: %(what)s") % {"what": last_text} if last_text else _("No messages in the last hour"))}


def channels():
    return list(AudioChannel.objects.filter(archived=False).order_by("name"))


def rail(active, items=None) -> dict:
    """The Audio rail: three music and three voice channels (always keeping the open one in view) and "See all"."""
    items = items if items is not None else [(c, music_status(c) if c.kind == AudioChannel.MUSIC else voice_status(c)) for c in channels()]
    groups = []
    for kind, title, all_label, view, icon in ((AudioChannel.MUSIC, _("Music"), _("See all music"), "music", MUSIC_ICON),
                                                (AudioChannel.VOICE, _("Voice"), _("See all voice"), "voice", MIC_ICON)):
        mine = [(c, s) for c, s in items if c.kind == kind]
        top = mine[:3]
        here = next(((c, s) for c, s in mine if c.id == active), None)
        if here and here not in top:
            top[-1] = here
        groups.append({"title": title, "all_label": all_label, "total": len(mine), "all_on": active == f"all-{view}",
                       "all_href": "?" + urlencode({"type": view}),
                       "items": [{"c": c, "s": s, "on": c.id == active, "icon": icon,
                                  "kind": kind, "eq": kind == AudioChannel.MUSIC and s["playing"],
                                  "live": kind == AudioChannel.VOICE and s["live"]} for c, s in top]})
    return {"groups": groups}


def channels_page(kind, q) -> dict:
    items = [(c, music_status(c) if c.kind == AudioChannel.MUSIC else voice_status(c)) for c in channels()]
    n_music = sum(c.kind == AudioChannel.MUSIC for c, _s in items)
    n_voice = len(items) - n_music
    ql = q.strip().lower()
    shown = [(c, s) for c, s in items if not ql or ql in f"{c.name} {c.notes}".lower()]
    sections = []
    for key, title in ((AudioChannel.MUSIC, _("Music")), (AudioChannel.VOICE, _("Voice"))):
        if kind not in ("all", key):
            continue
        cards = [{"c": c, "s": s} for c, s in shown if c.kind == key]
        if not cards:
            continue
        meta = (ngettext("%(n)d playing", "%(n)d playing", sum(x["s"]["playing"] for x in cards)) % {"n": sum(x["s"]["playing"] for x in cards)}
                if key == AudioChannel.MUSIC else ngettext("%(n)d live", "%(n)d live", sum(x["s"]["live"] for x in cards)) % {"n": sum(x["s"]["live"] for x in cards)})
        sections.append({"key": key, "title": title, "meta": meta, "cards": cards})
    return {"rail": rail(f"all-{'voice' if kind == 'voice' else 'music'}", items), "kind": kind, "q": q, "sections": sections,
            "count": _("%(m)d music · %(v)d voice") % {"m": n_music, "v": n_voice},
            "tabs": [(k, label, n, k == kind) for k, label, n in (("all", _("All"), len(items)), ("music", _("Music"), n_music), ("voice", _("Voice"), n_voice))],
            "icons": {"music": MUSIC_ICON, "voice": MIC_ICON}}


# ---------- a music channel ----------

def mosaic(tracks) -> list:
    arts = list(dict.fromkeys(t.artwork.url for t in tracks if t.artwork))
    return [arts[k % len(arts)] for k in range(4)] if arts else [None] * 4


def music_page(channel, view="now", pl=None, q="", sort="title", sheet="") -> dict:
    status = music_status(channel)
    state = status["state"]
    ch = state["channel"]
    track = state["track"]
    playing = status["playing"]
    entries = state["entries"]
    cur = state["cur"]
    total = sum(music._length(t) for _e, t in entries)
    queue = []
    for i, (entry, t) in enumerate(entries):
        is_cur, is_next = i == cur, i == cur + 1
        queue.append({"k": entry["k"], "n": i + 1, "t": t, "art": art(t), "dur": clock(music._length(t)), "cur": is_cur, "next": is_next,
                      "played": i < cur, "tag": (_("Now playing") if playing else _("Paused")) if is_cur else _("Next") if is_next else ""})
    if ch.repeat == AudioChannel.REPEAT_ALL:
        end = _("End of queue. Then it starts again from %(title)s.") % {"title": entries[0][1].title} if entries else _("End of queue.")
    elif ch.repeat == AudioChannel.REPEAT_ONE:
        end = _("Repeating %(title)s until you change it.") % {"title": track.title if track else _("this song")}
    else:
        end = _("End of queue. Music stops after %(title)s.") % {"title": entries[-1][1].title} if entries else _("End of queue.")

    all_tracks = list(Track.objects.all())
    by_id = {t.id: t for t in all_tracks}
    playlists = list(Playlist.objects.order_by("created_at", "id"))
    pl_tracks = {}
    for e in PlaylistTrack.objects.filter(playlist__in=playlists).order_by("position", "id"):
        if e.track_id in by_id:
            pl_tracks.setdefault(e.playlist_id, []).append(by_id[e.track_id])
    ql = q.strip().lower()

    def pl_card(p):
        ts = pl_tracks.get(p.id, [])
        return {"p": p, "name": p.name or _("Untitled playlist"), "tiles": mosaic(ts), "n": len(ts),
                "meta": f"{songs(len(ts))} · {mins(sum(music._length(t) for t in ts))}", "ids": ",".join(str(t.id) for t in ts)}

    def song_row(t, n=None):
        return {"t": t, "n": n, "art": art(t), "artist": t.artist or _("Unknown artist"), "album": t.album or _("Single"),
                "dur": clock(music._length(t)), "cur": bool(track) and t.id == track.id}

    pls = [pl_card(p) for p in playlists if pl_tracks.get(p.id) and (not ql or ql in (p.name or "").lower())]
    keys = {"title": lambda t: t.title.lower(), "artist": lambda t: ((t.artist or "~").lower(), t.title.lower()),
            "added": lambda t: -t.added_at.timestamp()}
    found = sorted((t for t in all_tracks if not ql or ql in f"{t.title} {t.artist} {t.album}".lower()), key=keys.get(sort, keys["title"]))
    detail = None
    playlist = next((p for p in playlists if str(p.id) == str(pl)), None)
    if view == "playlist" and playlist:
        detail = {**pl_card(playlist), "songs": [song_row(t, i + 1) for i, t in enumerate(pl_tracks.get(playlist.id, []))]}
    elif view == "playlist":
        view = "browse"

    sh = None
    kind, _sep, ref = sheet.partition(":")
    if kind == "pl":
        p = next((x for x in playlists if str(x.id) == ref), None)
        if p and pl_tracks.get(p.id):
            card = pl_card(p)
            sh = {"title": card["name"], "sub": _("Playlist · %(meta)s") % {"meta": card["meta"]}, "art": art(next((t for t in pl_tracks[p.id] if t.artwork), None)),
                  "ref": f"p{p.id}"}
    elif kind == "song" and ref.isdigit() and int(ref) in by_id:
        t = by_id[int(ref)]
        sh = {"title": t.title, "sub": _("Song · %(artist)s · %(len)s") % {"artist": t.artist or _("Unknown artist"), "len": clock(music._length(t))},
              "art": art(t), "ref": f"t{t.id}"}
    if sh:
        last = entries[-1][1].title if entries else None
        sh["opts"] = [("playnow", _("Play now"), _("Replaces the queue with this playlist") if kind == "pl" else _("Replaces the queue with just this song"), "M7 4v16l13-8z"),
                      ("playnext", _("Play next"), _("Right after %(title)s") % {"title": track.title if track else _("the current song")}, "M12 19V7 M6 12l6-6 6 6 M5 3h14"),
                      ("playlast", _("Add to end of queue"), _("After %(title)s") % {"title": last} if last else _("At the end of the queue"), "M4 6h12 M4 12h12 M4 18h8 M18 15v6 M15 18h6")]

    offset = state["offset"]
    length = music._length(track) if track else 1
    return {
        "ch": ch, "status": status, "view": view, "q": q, "sort": sort, "playing": playing, "track": track, "art": status["art"],
        "from": _("Playing from %(source)s") % {"source": ch.source} if ch.source else _("Playing a single song"),
        "artist_line": " · ".join(x for x in (track.artist if track else "", track.album if track else "") if x) if track else _("Pick something from Browse"),
        "pos": clock(offset), "left": clock(length - offset), "length": round(length, 2), "offset": round(offset, 2),
        "started_ms": ms(state["started_at"]), "pct": round(offset / length * 100, 2) if track else 0,
        "repeat": ch.repeat, "shuffle": ch.shuffle, "volume": ch.volume,
        "repeat_label": {"off": _("Repeat is off"), "all": _("Repeating the queue"), "one": _("Repeating this song")}[ch.repeat],
        "repeat_chip": {"off": "", "all": _("Repeating queue"), "one": _("Repeating this song")}[ch.repeat],
        "queue": queue, "end_note": end,
        "queue_meta": _("Song %(n)d of %(total)d · %(len)s") % {"n": cur + 1, "total": len(entries), "len": mins(total)} if entries else _("Empty"),
        "up_count": f"{cur + 1}/{len(entries)}" if entries else "0/0",
        "pls": pls, "songs": [song_row(t) for t in found], "songs_head": songs(len(found)) if ql else _("All songs"),
        "sorts": [(k, label, k == sort) for k, label in (("title", _("A–Z")), ("artist", _("Artist")), ("added", _("Newest")))],
        "detail": detail, "sheet": sh,
    }


def music_url(channel, view="now", **params) -> str:
    from django.urls import reverse
    query = {"view": view, **{k: v for k, v in params.items() if v not in (None, "")}}
    return reverse("audio_music", args=[channel.id]) + ("?" + urlencode(query) if query != {"view": "now"} else "")


# ---------- a voice channel ----------

def voice_page(channel) -> dict:
    status = voice_status(channel)
    rows = []
    if status["who"]:
        rows.append({"kind": "live", "who": status["who"]["first"], "ini": status["who"]["initials"], "face": face(status["who"]["name"]),
                     "what": _("Talking now…")})
    for e in status["log"]:
        if e["kind"] == "voice":
            rows.append({"kind": "voice", "who": e["first"], "ini": e["initials"], "face": face(e["name"]),
                         "what": f"{clock(e['len'])} · {ago(e['at'])}"})
        else:
            rows.append({"kind": "call", "who": _("Order call"), "what": f"“{e['text']}” · {ago(e['at'])}", "url": e["url"], "text": e["text"]})
    last_call = next((e for e in status["log"] if e["kind"] == "call"), None)
    return {"ch": channel, "status": status, "rows": rows, "last_call": last_call, "where": channel.notes}
