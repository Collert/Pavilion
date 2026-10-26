"""
The music library: songs uploaded in the admin's Library (design: AdminLibrary). The browser reads each file's tags
(title, artist, album, year, genre, artwork, length: admin_portal/static/admin_portal/library.js with audio/tags.js)
before it uploads, so the server never opens a song: the file is stored as is under music/ in the media storage
and what the tags said is kept on the Track. Screens play songs straight from the storage.
"""
import posixpath

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Sum

from .models import Playlist, PlaylistTrack, Track

EXTENSIONS = {".mp3": "MP3", ".m4a": "M4A", ".aac": "AAC", ".flac": "FLAC", ".ogg": "OGG", ".oga": "OGG", ".opus": "OPUS", ".wav": "WAV"}
MAX_SIZE = 200 * 1024 * 1024  # bytes, per song
ART_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
MAX_ART = 10 * 1024 * 1024


def limit_bytes() -> int:
    """The space songs may use (Store info's "Space for music")."""
    from admin_portal import store_info
    return store_info.music_limit_bytes()


def used_bytes() -> int:
    return Track.objects.aggregate(n=Sum("size"))["n"] or 0


def format_of(name) -> str:
    return EXTENSIONS.get(posixpath.splitext(name or "")[1].lower(), "")


def _text(value, n):
    return str(value or "").strip()[:n]


def _year(value):
    text = _text(value, 4)
    return int(text) if text.isdigit() and 1000 <= int(text) <= 2999 else None


def add_track(upload, meta, artwork=None, playlist=None) -> Track:
    """Keep an uploaded song: the file as it came, with the tags the browser read (`meta`: title, artist, album,
    year, genre, duration in seconds, bitrate in kbps). A song without a title is named after its file."""
    stem = posixpath.splitext(posixpath.basename(upload.name))[0]
    try:
        duration = max(0.0, float(meta.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0.0
    try:
        bitrate = max(0, int(float(meta.get("bitrate") or 0)))
    except (TypeError, ValueError):
        bitrate = 0
    track = Track(title=_text(meta.get("title"), 200) or stem[:200], artist=_text(meta.get("artist"), 200),
                  album=_text(meta.get("album"), 200), year=_year(meta.get("year")), genre=_text(meta.get("genre"), 80),
                  duration=duration, bitrate=bitrate, format=format_of(upload.name), size=upload.size or 0,
                  original_name=posixpath.basename(upload.name)[:255])
    track.file.save(posixpath.basename(upload.name), upload, save=False)
    if artwork:
        track.artwork.save(f"{posixpath.basename(track.file.name)}.{ART_TYPES.get(artwork.content_type, 'jpg')}", artwork, save=False)
    track.save()
    if playlist:
        append(playlist, [track.id])
    return track


def append(playlist, track_ids):
    """Add songs to the end of a playlist (songs already in it are skipped). Returns how many were added."""
    have = set(playlist.entries.values_list("track_id", flat=True))
    start = (playlist.entries.aggregate(n=Max("position"))["n"] or 0) + 1
    fresh = [i for i in dict.fromkeys(track_ids) if i not in have]
    PlaylistTrack.objects.bulk_create(PlaylistTrack(playlist=playlist, track_id=i, position=start + n) for n, i in enumerate(fresh))
    return len(fresh)


def reorder(playlist, track_ids):
    """Put a playlist's songs in this order (songs left out keep their place after the ones given)."""
    entries = {e.track_id: e for e in playlist.entries.all()}
    order = [i for i in track_ids if i in entries] + [i for i in entries if i not in track_ids]
    for position, track_id in enumerate(order):
        entries[track_id].position = position
    PlaylistTrack.objects.bulk_update(entries.values(), ["position"])


@transaction.atomic
def delete_tracks(ids) -> int:
    """Delete songs, their files and artwork (from the media storage) and their places in playlists."""
    tracks = list(Track.objects.filter(id__in=ids))
    for track in tracks:
        files = [f for f in (track.file, track.artwork) if f]
        transaction.on_commit(lambda files=files: [f.storage.delete(f.name) for f in files])
    Track.objects.filter(id__in=[t.id for t in tracks]).delete()
    return len(tracks)


def new_playlist(track_ids=()) -> Playlist:
    playlist = Playlist.objects.create(name="")
    append(playlist, list(track_ids))
    return playlist
