"""
The music library: songs are files under files/music/ in the media storage (a local folder in development, the S3
bucket or a NAS mount in production). scan() adds the files it finds that aren't in the library yet, reading their
tags (title, artist, album, year, genre, length, artwork) with mutagen, and drops songs whose files are gone.
Run it with `manage.py scan_music`, from the admin, or let development pick new files up on its own (audio/music.py).
"""
import logging
import os
import posixpath

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from .models import Track

logger = logging.getLogger(__name__)

MUSIC_DIR = "files/music"
SKIP_DIRS = {"artwork"}
EXTENSIONS = {".mp3": "MP3", ".m4a": "M4A", ".aac": "AAC", ".flac": "FLAC", ".ogg": "OGG", ".oga": "OGG", ".opus": "OPUS", ".wav": "WAV"}


def ensure_folder():
    """Make files/music/ when the media storage is a folder on this machine (S3 has no folders to make)."""
    try:
        os.makedirs(default_storage.path(MUSIC_DIR), exist_ok=True)
    except (NotImplementedError, OSError):
        pass


def _files(folder=MUSIC_DIR):
    try:
        dirs, files = default_storage.listdir(folder)
    except (FileNotFoundError, OSError):
        return
    for name in files:
        if posixpath.splitext(name)[1].lower() in EXTENSIONS:
            yield posixpath.join(folder, name)
    for name in dirs:
        if name not in SKIP_DIRS:
            yield from _files(posixpath.join(folder, name))


def _first(tags, key):
    value = tags.get(key) if tags else None
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _artwork(path):
    """The cover picture inside the file, as (bytes, extension), or None."""
    import mutagen
    try:
        with default_storage.open(path) as f:
            audio = mutagen.File(f)
    except Exception:
        return None
    if audio is None:
        return None
    for picture in getattr(audio, "pictures", []) or []:  # FLAC
        return picture.data, "png" if picture.mime == "image/png" else "jpg"
    tags = getattr(audio, "tags", None) or {}
    for key in list(tags.keys()):
        if str(key).startswith("APIC"):  # MP3 (ID3)
            frame = tags[key]
            return frame.data, "png" if frame.mime == "image/png" else "jpg"
    covers = tags.get("covr") if hasattr(tags, "get") else None  # M4A
    if covers:
        return bytes(covers[0]), "png" if getattr(covers[0], "imageformat", 0) == 14 else "jpg"
    return None


def read_tags(path) -> dict:
    """What a file's tags say, with the file name as the title when it has none."""
    import mutagen
    stem = posixpath.splitext(posixpath.basename(path))[0]
    info = {"title": stem.replace("_", " ").strip() or stem, "artist": "", "album": "", "year": None, "genre": "",
            "duration": 0.0, "bitrate": 0, "format": EXTENSIONS.get(posixpath.splitext(path)[1].lower(), "")}
    try:
        with default_storage.open(path) as f:
            audio = mutagen.File(f, easy=True)
    except Exception:
        logger.warning("Couldn't read the tags of %s", path)
        return info
    if audio is None:
        return info
    tags = audio.tags or {}
    info["title"] = _first(tags, "title")[:200] or info["title"]
    info["artist"] = _first(tags, "artist")[:200]
    info["album"] = _first(tags, "album")[:200]
    info["genre"] = _first(tags, "genre")[:80]
    year = _first(tags, "date")[:4]
    info["year"] = int(year) if year.isdigit() else None
    info["duration"] = float(getattr(audio.info, "length", 0) or 0)
    info["bitrate"] = int((getattr(audio.info, "bitrate", 0) or 0) // 1000)
    return info


def scan() -> dict:
    """Add new files to the library and drop songs whose files are gone. Returns {"added", "removed", "total"}."""
    ensure_folder()
    known = {t.file.name: t for t in Track.objects.all()}
    found = set()
    added = 0
    for path in _files():
        found.add(path)
        if path in known:
            continue
        info = read_tags(path)
        try:
            size = default_storage.size(path)
        except Exception:
            size = 0
        track = Track(title=info["title"], artist=info["artist"], album=info["album"], year=info["year"], genre=info["genre"],
                      duration=info["duration"], bitrate=info["bitrate"], format=info["format"], size=size)
        track.file.name = path
        track.save()
        cover = _artwork(path)
        if cover:
            data, ext = cover
            track.artwork.save(f"{track.id}.{ext}", ContentFile(data), save=True)
        added += 1
    gone = [t.id for name, t in known.items() if name not in found]
    Track.objects.filter(id__in=gone).delete()
    return {"added": added, "removed": len(gone), "total": Track.objects.count()}
