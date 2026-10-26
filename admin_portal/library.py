"""
Library (design: AdminLibrary): the store's songs and playlists (audio.Track, audio.Playlist). What's shown lives in
the address (?view=all|needs|p<id>, q, sort, song=<id> for the details drawer, add=1 for "Add songs" on a
playlist, aq to search it); buttons post an op and come back to the same view. Songs arrive through the Add music
dialog (static/admin_portal/library.js reads their tags in the browser and uploads them to `upload`).
"""
from urllib.parse import urlencode

from django.db.models import Count, Prefetch
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from audio import library
from audio.models import Playlist, PlaylistTrack, Track

SORTS = [("added", _("Recently added")), ("title", _("Title")), ("artist", _("Artist")), ("album", _("Album")), ("dur", _("Longest first"))]
NOTE = "M9 18V5l11-2v13 M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z M20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"
WARN = "M12 4 2.5 20h19z M12 10v4 M12 17h.01"


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def state(data) -> dict:
    """What's on screen, from the address (GET) or the hidden fields a form posts back."""
    view = data.get("view", "all")
    if view not in ("all", "needs") and not (view.startswith("p") and _int(view[1:])):
        view = "all"
    sort = data.get("sort", "added")
    return {"view": view, "q": data.get("q", "").strip()[:80], "sort": sort if sort in dict(SORTS) else "added"}


def url(st, **extra) -> str:
    params = {"view": st["view"]}
    if st["q"]:
        params["q"] = st["q"]
    if st["sort"] != "added":
        params["sort"] = st["sort"]
    params.update({k: v for k, v in extra.items() if v not in (None, "")})
    return "?" + urlencode(params)


def playlist_of(st):
    if st["view"].startswith("p"):
        return Playlist.objects.filter(pk=_int(st["view"][1:])).first()
    return None


def mins(seconds) -> str:
    seconds = int(round(seconds or 0))
    hours, minutes = seconds // 3600, round((seconds % 3600) / 60)
    return _("%(h)d hr %(m)d min") % {"h": hours, "m": minutes} if hours else _("%(m)d min") % {"m": minutes}


def clock(seconds) -> str:
    seconds = int(round(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def songs(n) -> str:
    return ngettext("%(n)d song", "%(n)d songs", n) % {"n": n}


def needs_info(t) -> bool:
    return not t.artist or not t.album or not t.artwork


def mosaic(tracks) -> list:
    """Four artwork addresses for a playlist's tile (repeated when it has fewer pictures; None for blanks)."""
    arts = list(dict.fromkeys(t.artwork.url for t in tracks if t.artwork))
    if not arts:
        return [None] * 4
    return [arts[k % len(arts)] for k in range(4)]


def size_text(n) -> str:
    return f"{n / 1000 ** 2:.1f} MB" if n < 1000 ** 3 else f"{n / 1000 ** 3:.2f} GB"


def file_meta(t) -> str:
    quality = _("lossless") if t.format in ("WAV", "FLAC") else (_("%(n)d kbps") % {"n": t.bitrate} if t.bitrate else "")
    added = date_format(timezone.localtime(t.added_at), "M j")
    return " · ".join(x for x in (t.format, quality, size_text(t.size), clock(t.duration), _("added %(day)s") % {"day": added}) if x)


def page(st, song=None, add=False, aq="") -> dict:
    tracks = list(Track.objects.annotate(n_pls=Count("playlists")).order_by("-added_at", "title"))
    by_id = {t.id: t for t in tracks}
    playlists = list(Playlist.objects.prefetch_related(Prefetch("entries", queryset=PlaylistTrack.objects.order_by("position", "id"))).order_by("created_at", "id"))
    pl_tracks = {p.id: [by_id[e.track_id] for e in p.entries.all() if e.track_id in by_id] for p in playlists}
    pl = next((p for p in playlists if st["view"] == f"p{p.id}"), None)
    view = "pl" if pl else ("needs" if st["view"] == "needs" else "all")

    n_needs = sum(needs_info(t) for t in tracks)
    smart = [{"key": "all", "label": _("All songs"), "count": len(tracks), "icon": NOTE, "color": "#1F4A3A", "warn": False},
             {"key": "needs", "label": _("Missing info"), "count": n_needs, "icon": WARN, "color": "#C9821A", "warn": bool(n_needs)}]
    for s in smart:
        s["on"] = view == s["key"]
        s["href"] = url({**st, "view": s["key"]})
    nav = [{"id": p.id, "name": p.name or _("Untitled playlist"), "on": pl is not None and p.id == pl.id,
            "sub": f"{songs(len(pl_tracks[p.id]))} · {mins(sum(t.duration for t in pl_tracks[p.id]))}",
            "tiles": mosaic(pl_tracks[p.id]), "href": url({"view": f"p{p.id}", "q": "", "sort": st["sort"]})} for p in playlists]

    used, limit = library.used_bytes(), library.limit_bytes()
    storage = {"text": _("%(used)s GB of %(limit)s GB") % {"used": f"{used / 1000 ** 3:.2f}", "limit": f"{limit / 1000 ** 3:g}"},
               "pct": max(2, min(100, round(used / limit * 100, 1))) if limit else 100}

    v = {"is_pl": bool(pl)}
    if pl:
        rows = pl_tracks[pl.id]
        n = len(rows)
        v.update(title=pl.name or _("Untitled playlist"), name=pl.name, desc=pl.description, id=pl.id, tiles=mosaic(rows), add_href=url(st, add=1),
                 sub=f"{songs(n)} · {mins(sum(t.duration for t in rows))}" + (" · " + _("Drag to reorder") if n > 1 else ""))
    else:
        rows = [t for t in tracks if needs_info(t)] if view == "needs" else list(tracks)
        q = st["q"].lower()
        if q:
            rows = [t for t in rows if q in f"{t.title} {t.artist} {t.album}".lower()]
        last = "~"  # blanks sort last
        keys = {"added": lambda t: (-t.added_at.timestamp(), t.title.lower()), "title": lambda t: t.title.lower(),
                "artist": lambda t: ((t.artist or last).lower(), t.title.lower()), "album": lambda t: ((t.album or last).lower(), t.title.lower()),
                "dur": lambda t: -t.duration}
        rows.sort(key=keys[st["sort"]])
        if view == "needs":
            v.update(title=_("Missing info"), sub=_("Songs without an artist, album or artwork. Click one to fill it in."))
        else:
            v.update(title=_("All songs"), sub=" · ".join((songs(len(tracks)), mins(sum(t.duration for t in tracks)),
                                                          ngettext("%(n)d playlist", "%(n)d playlists", len(playlists)) % {"n": len(playlists)})))
        if q:
            v["sub"] = ngettext("%(n)d match for “%(q)s”", "%(n)d matches for “%(q)s”", len(rows)) % {"n": len(rows), "q": st["q"]}

    out_rows = [{"t": t, "n": k + 1, "title": t.title or _("Untitled"), "artist": t.artist, "album": t.album, "dur": clock(t.duration),
                 "art": t.artwork.url if t.artwork else "", "src": t.file.url,
                 "pls": ngettext("%(n)d playlist", "%(n)d playlists", t.n_pls) % {"n": t.n_pls} if t.n_pls else "—",
                 "added": date_format(timezone.localtime(t.added_at), "M j"),
                 "song_href": url(st, song=t.id)} for k, t in enumerate(rows)]

    if pl:
        empty = {"head": _("This playlist is empty"), "text": _("Add songs from your library. You can drag them into order afterwards."),
                 "btn": _("Add songs"), "href": url(st, add=1)}
    elif st["q"]:
        empty = {"head": _("No matches"), "text": _("Nothing in this list matches “%(q)s”.") % {"q": st["q"]},
                 "btn": _("Clear search"), "href": url({**st, "q": ""})}
    elif view == "needs":
        empty = {"head": _("Everything’s tagged"), "text": _("Every song has an artist, album and artwork."),
                 "btn": _("Back to all songs"), "href": url({**st, "view": "all"})}
    else:
        empty = {"head": _("No music yet"), "text": _("Upload songs your restaurant owns or is licensed to play."),
                 "btn": _("Add music"), "upload": True}

    targets = [{"id": p.id, "name": p.name or _("Untitled playlist"), "has": ",".join(str(t.id) for t in pl_tracks[p.id])}
               for p in playlists if not pl or p.id != pl.id]

    drawer = None
    track = by_id.get(_int(song))
    if track:
        in_pls = {p.id for p in playlists if track in pl_tracks[p.id]}
        n = len(in_pls)
        drawer = {"t": track, "art": track.artwork.url if track.artwork else "", "year": track.year or "",
                  "pls": [{"id": p.id, "name": p.name or _("Untitled playlist"), "on": p.id in in_pls} for p in playlists],
                  "file": track.original_name or track.file.name.rsplit("/", 1)[-1], "meta": file_meta(track),
                  "del": _("Delete from library") + (" " + ngettext("and %(n)d playlist", "and %(n)d playlists", n) % {"n": n} if n else ""),
                  "close": url(st)}

    adding = None
    if add and pl:
        have = {t.id for t in pl_tracks[pl.id]}
        aql = aq.strip().lower()
        adding = {"title": _("Add songs to %(name)s") % {"name": pl.name or _("this playlist")}, "aq": aq, "close": url(st),
                  "rows": [{"t": t, "artist": t.artist or _("Unknown artist"), "dur": clock(t.duration), "art": t.artwork.url if t.artwork else "",
                            "secs": round(t.duration)} for t in tracks
                           if t.id not in have and (not aql or aql in f"{t.title} {t.artist} {t.album}".lower())]}

    return {"st": st, "view": view, "smart": smart, "nav": nav, "storage": storage, "v": v, "rows": out_rows,
            "sorts": [(k, label, k == st["sort"]) for k, label in SORTS], "empty": empty, "targets": targets,
            "drawer": drawer, "adding": adding, "pl_opts": [(p.id, p.name or _("Untitled playlist")) for p in playlists],
            "limit_left": max(0, limit - used), "max_size": library.MAX_SIZE}


# ---------- actions ----------

def _ids(post, name="sel"):
    return [i for i in (_int(x) for x in post.getlist(name)) if i]


def apply(post, files) -> tuple[str, dict]:
    """Do what a button asked. Returns (toast, where to go: changes to the view state plus song/add)."""
    st = state(post)
    op, _sep, arg = post.get("op", "").partition(":")
    pl = playlist_of(st)
    sel = _ids(post)
    if op == "newpl":
        playlist = library.new_playlist(sel)
        msg = _("New playlist with %(songs)s. Give it a name.") % {"songs": songs(len(sel))} if sel else ""
        return msg, {"view": f"p{playlist.id}", "q": ""}
    if op == "addto" and sel:
        target = Playlist.objects.filter(pk=_int(arg)).first()
        if target:
            added = library.append(target, sel)
            name = target.name or _("the playlist")
            if added:
                return _("%(songs)s added to %(name)s.") % {"songs": songs(added), "name": name}, {}
            return _("Those songs are already in %(name)s.") % {"name": name}, {}
    if op == "plremove" and pl:
        ids = [_int(arg)] if arg else sel
        gone = PlaylistTrack.objects.filter(playlist=pl, track_id__in=ids).delete()[0]
        if len(ids) == 1 and gone:
            title = Track.objects.filter(pk=ids[0]).values_list("title", flat=True).first()
            return _("%(title)s removed from %(name)s.") % {"title": title, "name": pl.name or _("the playlist")}, {}
        return _("%(songs)s removed from %(name)s.") % {"songs": songs(gone), "name": pl.name or _("the playlist")}, {}
    if op == "delete" and sel:
        n = library.delete_tracks(sel)
        return _("%(songs)s deleted from the library and its playlists.") % {"songs": songs(n)}, {}
    if op == "plsave" and pl:
        pl.name = post.get("pl_name", "").strip()[:40]
        pl.description = post.get("pl_desc", "").strip()[:80]
        pl.save(update_fields=["name", "description"])
        return "", {}
    if op == "pldelete" and pl:
        name = pl.name or _("Playlist")
        pl.delete()
        return _("%(name)s deleted. The songs are still in your library.") % {"name": name}, {"view": "all"}
    if op == "reorder" and pl:
        library.reorder(pl, [i for i in (_int(x) for x in post.get("order", "").split(",")) if i])
        return "", {}
    if op == "addsongs" and pl:
        added = library.append(pl, sel)
        return (_("%(songs)s added to %(name)s.") % {"songs": songs(added), "name": pl.name or _("the playlist")}) if added else "", {}
    if op == "track":
        return save_track(post, files)
    if op == "trackdel":
        track = Track.objects.filter(pk=_int(post.get("song"))).first()
        if track:
            library.delete_tracks([track.id])
            return _("%(title)s deleted.") % {"title": track.title or _("Song")}, {}
    if op == "uploaded":
        n = _int(post.get("n"), 0)
        target = Playlist.objects.filter(pk=_int(post.get("pl"))).first()
        if target:
            msg = _("%(songs)s added to the library and %(name)s.") % {"songs": songs(n), "name": target.name or _("the playlist")}
            return msg, {"view": f"p{target.id}", "q": "", "sort": "added"}
        return _("%(songs)s added to the library.") % {"songs": songs(n)}, {"view": "all", "q": "", "sort": "added"}
    return "", {}


def save_track(post, files) -> tuple[str, dict]:
    track = Track.objects.filter(pk=_int(post.get("song"))).first()
    if not track:
        return "", {}
    track.title = post.get("title", "").strip()[:200] or track.title
    track.artist = post.get("artist", "").strip()[:200]
    track.album = post.get("album", "").strip()[:200]
    track.genre = post.get("genre", "").strip()[:80]
    track.year = library._year(post.get("year"))
    art = files.get("artwork")
    if art and art.content_type in library.ART_TYPES and art.size <= library.MAX_ART:
        old = track.artwork.name if track.artwork else ""
        track.artwork.save(f"{track.id}.{library.ART_TYPES[art.content_type]}", art, save=False)
        if old:
            track.artwork.storage.delete(old)
    elif post.get("art_clear") and track.artwork:
        track.artwork.storage.delete(track.artwork.name)
        track.artwork = ""
    track.save()
    want = set(_ids(post, "pls"))
    have = set(PlaylistTrack.objects.filter(track=track).values_list("playlist_id", flat=True))
    PlaylistTrack.objects.filter(track=track, playlist_id__in=have - want).delete()
    for playlist in Playlist.objects.filter(pk__in=want - have):
        library.append(playlist, [track.id])
    return _("%(title)s updated.") % {"title": track.title}, {}


def upload(post, files) -> tuple[int, dict]:
    """One song from the Add music dialog. Returns (status, JSON)."""
    song = files.get("file")
    if not song:
        return 400, {"message": _("No file.")}
    if not library.format_of(song.name):
        return 400, {"message": _("%(name)s isn’t an audio file we can play.") % {"name": song.name}}
    if song.size > library.MAX_SIZE:
        return 400, {"message": _("%(name)s is over 200 MB.") % {"name": song.name}}
    if library.used_bytes() + song.size > library.limit_bytes():
        return 400, {"message": _("The library is full. Delete some songs to make room.")}
    if post.get("rights") != "1":
        return 400, {"message": _("Confirm you own these songs or are licensed to play them.")}
    art = files.get("artwork")
    if art and (art.content_type not in library.ART_TYPES or art.size > library.MAX_ART):
        art = None
    playlist = Playlist.objects.filter(pk=_int(post.get("pl"))).first()
    track = library.add_track(song, post, artwork=art, playlist=playlist)
    return 200, {"id": track.id, "title": track.title}
