"""Endpoints screens and staff use for audio channels (the pages come with the admin's Audio channels and Library)."""
import json

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from django.shortcuts import render

from pos_server.devices import api_staff_required, staff_required

from . import music
from .library import scan
from .models import AudioChannel, Playlist


@require_GET
def now(request, id):
    """A music channel's song and position, for the screens that play it (public: screens can be)."""
    channel = get_object_or_404(AudioChannel, pk=id, archived=False)
    if channel.kind != AudioChannel.MUSIC:
        raise Http404
    return JsonResponse(music.as_json(channel, request))


@require_POST
@api_staff_required
def control(request, id):
    """{"action": "play"|"pause"|"next"|"previous"|"playlist", "playlist": id or null, "shuffle": bool}"""
    channel = get_object_or_404(AudioChannel, pk=id, archived=False, kind=AudioChannel.MUSIC)
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        body = {}
    action = body.get("action")
    if action not in ("play", "pause", "next", "previous", "playlist"):
        return JsonResponse({"message": _("Unknown action.")}, status=400)
    playlist = Playlist.objects.filter(pk=body.get("playlist")).first() if body.get("playlist") else None
    channel = music.control(channel, action, playlist=playlist, shuffle=body.get("shuffle"))
    return JsonResponse(music.as_json(channel, request))


@staff_required
def talk(request, id):
    """Push-to-talk on a voice channel (a bare page until the talk screen is designed)."""
    channel = get_object_or_404(AudioChannel, pk=id, archived=False, kind=AudioChannel.VOICE)
    return render(request, "audio/talk.html", {"channel": channel})


@require_POST
@api_staff_required
def rescan(request):
    """Look for new songs in files/music/."""
    return JsonResponse(scan())
