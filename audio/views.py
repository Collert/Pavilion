"""
The Audio app (designs: AudioChannels, AudioMusic, AudioVoice; what they show is worked out in pages.py) and the
endpoints screens use. Any staff member can play music and talk; the Library and channels are set up in the admin.
"""
import json
import time

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from pos_server.devices import api_app_required, app_required, safe_next_url

from . import music, pages, voice
from .models import AudioChannel

CHIME = "misc_tools/announcement_chimes/a320-tritone-chime-104562.mp3"


@require_GET
def now(request, id):
    """A music channel's song and position, for the screens that play it (public: screens can be)."""
    channel = get_object_or_404(AudioChannel, pk=id, archived=False)
    if channel.kind != AudioChannel.MUSIC:
        raise Http404
    return JsonResponse(music.as_json(channel, request))


@require_POST
@api_app_required("app.audio")
def control(request, id):
    """{"op": one of music.OPS, "ids": [track ids], "key": queue entry, "value": ..., "source": "..."}"""
    channel = get_object_or_404(AudioChannel, pk=id, archived=False, kind=AudioChannel.MUSIC)
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        body = {}
    op = body.get("op")
    if op not in music.OPS:
        return JsonResponse({"message": _("Unknown action.")}, status=400)
    ids = [i for i in body.get("ids") or [] if isinstance(i, int)]
    music.control(channel, op, ids=ids, key=body.get("key"), value=body.get("value"), source=str(body.get("source") or ""))
    return JsonResponse(music.as_json(channel, request))


def _page(request, template, context):
    from admin_portal.models import StoreSettings
    return render(request, template, {"now_ms": int(time.time() * 1000), "store": StoreSettings.load(), **context})


@app_required("app.audio")
def index(request):
    """All channels, or one type, with a search."""
    kind = request.GET.get("type", "all")
    kind = kind if kind in ("all", "music", "voice") else "all"
    data = pages.channels_page(kind, request.GET.get("q", "")[:60])
    return _page(request, "audio/channels.html", {"p": data, "rail": data["rail"], "section": kind})


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@app_required("app.audio")
def music_channel(request, id):
    """A music channel: now playing and the queue, browsing the Library, a playlist. Buttons post an op and come back."""
    channel = get_object_or_404(AudioChannel, pk=id, archived=False, kind=AudioChannel.MUSIC)
    if request.method == "POST":
        toast = _music_op(request, channel)
        if toast:
            messages.success(request, toast)
        return redirect(safe_next_url(request, request.POST.get("back")) or reverse("audio_music", args=[channel.id]))
    view = request.GET.get("view", "now")
    view = view if view in ("now", "browse", "playlist") else "now"
    data = pages.music_page(channel, view, pl=request.GET.get("pl"), q=request.GET.get("q", "")[:60],
                            sort=request.GET.get("sort", "title"), sheet=request.GET.get("sheet", ""))
    query = request.GET.copy()
    query.pop("sheet", None)  # after an action from the sheet, come back without it
    back = request.path + ("?" + query.urlencode() if query else "")
    return _page(request, "audio/music.html", {"m": data, "rail": pages.rail(channel.id), "back": back, "section": "music",
                                                "close_sheet": back})


def _music_op(request, channel) -> str:
    """Do a music page's button (op, "name:arg"). arg is a queue entry's key, or what to queue: p<playlist id> or
    t<song id> (shuffleplay:p<id> plays a playlist shuffled). Returns the toast."""
    from .models import Playlist, Track
    name, _sep, arg = request.POST.get("op", "").partition(":")
    value = request.POST.get("value")
    if name in ("seek", "volume"):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return ""
    ids, label, source = [], "", ""
    if arg[:1] == "p" and _int(arg[1:]):
        playlist = Playlist.objects.filter(pk=_int(arg[1:])).first()
        if not playlist:
            return ""
        ids = [t.id for t in playlist.ordered_tracks()]
        label = source = playlist.name or _("Untitled playlist")
    elif arg[:1] == "t" and _int(arg[1:]):
        track = Track.objects.filter(pk=_int(arg[1:])).first()
        if not track:
            return ""
        ids, label = [track.id], track.title
    if name == "shuffleplay" and ids:
        import random
        random.shuffle(ids)
        music.control(channel, "playnow", ids=ids, source=_("%(name)s (shuffled)") % {"name": source})
        return _("Shuffling %(what)s.") % {"what": label}
    if name not in music.OPS:
        return ""
    result = music.control(channel, name, ids=ids, key=None if ids else _int(arg), value=value, source=source)
    if name == "playnow" and ids:
        return _("Playing %(what)s.") % {"what": label} if source else _("Playing %(what)s. It’s the only song in the queue.") % {"what": label}
    if name == "playnext" and ids:
        return _("%(what)s will play next.") % {"what": label}
    if name == "playlast" and ids:
        return _("%(what)s added to the end of the queue.") % {"what": label}
    if name == "clear":
        return _("Queue cleared. Only the current song is left.")
    if name == "shuffle" and result == "shuffled":
        return _("Up next shuffled.")
    return ""


@app_required("app.audio")
def voice_channel(request, id):
    """A voice channel: push-to-talk, a chime, and the last hour."""
    channel = get_object_or_404(AudioChannel, pk=id, archived=False, kind=AudioChannel.VOICE)
    if request.method == "POST":
        if request.POST.get("op") == "chime" and not voice.talking(channel.id):
            voice.broadcast(channel.id, {"type": "clip", "url": static(CHIME), "text": "", "chime": True})
        return redirect("audio_voice", id=channel.id)
    return _page(request, "audio/voice.html", {"v": pages.voice_page(channel), "rail": pages.rail(channel.id), "section": "voice",
                                                "chime": static(CHIME)})


@app_required("app.audio")
def talk(request, id):
    """The old push-to-talk address: the voice channel's page."""
    return redirect("audio_voice", id=id)

