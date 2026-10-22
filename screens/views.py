import json
import time

from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from pavilion.realtime import publish
from pos_server.devices import api_staff_required, staff_required

from . import registry
from .layout import render_screen
from .models import Screen, ScreenSession


@staff_required
def index(request):
    """Apps → Screens: every screen. One with sessions opens its session picker; the rest open straight away."""
    return render(request, "screens/index.html", {"screens": Screen.objects.all(), "nav": "screens"})


@staff_required
def sessions(request, slug):
    """A screen's sessions: open one on this display, or start a new one (POST name)."""
    screen = get_object_or_404(Screen, slug=slug)
    if not screen.sessions:
        return redirect("screen", slug=screen.slug)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:60]
        if name:
            session = ScreenSession.objects.create(screen=screen, name=name)
            return redirect("screen_session", slug=screen.slug, code=session.code)
    return render(request, "screens/sessions.html", {"screen": screen, "sessions": screen.screen_sessions.all(), "nav": "screens"})


def _allowed(request, screen):
    return screen.public or (request.user.is_authenticated and request.user.is_staff)


def show(request, slug, code=None):
    """
    A screen, rendered from its layout on the server. static/js/live-page.js keeps it current: it refetches this page
    on the live widgets' SSE topics (and "screens", when someone edits a layout) and swaps in the widgets that changed.
    A session (/screens/<slug>/s/<code>) shows only its own register's order summary, and its code until a register pairs.
    """
    screen = get_object_or_404(Screen, slug=slug)
    if not _allowed(request, screen):
        return redirect_to_login(request.get_full_path())
    session = None
    if code is not None:
        session = ScreenSession.objects.filter(screen=screen, code=code).first()
        if session is None:  # removed in the admin: pick or start another
            return redirect("screen_sessions", slug=screen.slug)
        ScreenSession.objects.filter(pk=session.pk).update(last_seen=timezone.now())
        request.screen_session = session
    elif screen.sessions and request.user.is_authenticated and request.user.is_staff:
        return redirect("screen_sessions", slug=screen.slug)
    rendered = render_screen(screen, request)
    topics = ["screens", *rendered.topics] + (["popups"] if session and "popups" not in rendered.topics else [])
    return render(request, "screens/screen.html", {
        "screen": screen, "session": session,
        "rendered": rendered,
        "topics": topics,
        "now_ms": int(time.time() * 1000),
    })


def _json(request):
    try:
        body = json.loads(request.body)
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


@require_POST
@api_staff_required
def pair(request):
    """A register pairs with a session by its code ({"code"}), or lets it go ({"code", "unpair": true})."""
    code = str(_json(request).get("code", "")).strip().upper().replace(" ", "")
    if len(code) == 6 and "-" not in code:
        code = f"{code[:3]}-{code[3:]}"
    session = ScreenSession.objects.select_related("screen").filter(code=code).first()
    if session is None:
        return JsonResponse({"message": _("No screen has that code. Check the code on the screen.")}, status=404)
    if _json(request).get("unpair"):
        ScreenSession.objects.filter(pk=session.pk).update(paired_by="", paired_at=None)
        cache.delete(summary_key(session.code))
    else:
        who = request.user.get_full_name() or request.user.username
        ScreenSession.objects.filter(pk=session.pk).update(paired_by=who[:120], paired_at=timezone.now())
    publish("popups")
    return JsonResponse({"code": session.code, "name": session.name, "screen": session.screen.name})


def summary_key(code):
    return f"screens:order-summary:{code}"


def _cents(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


@require_POST
@api_staff_required
def summary(request, code):
    """The paired register's order while it's taking payment ({"paying": true, "lines", totals in cents...}); anything
    else clears it. Only this session's order-summary pop-up shows it."""
    session = get_object_or_404(ScreenSession, code=code)
    body = _json(request)
    key = summary_key(session.code)
    if body.get("paying") and body.get("lines"):
        lines = [{"title": str(l.get("title", ""))[:80], "qty": _cents(l.get("qty")), "mods": str(l.get("mods", ""))[:160],
                  "total": _cents(l.get("total"))} for l in body["lines"][:40] if isinstance(l, dict)]
        cache.set(key, {"lines": lines, "name": str(body.get("name", ""))[:60], "number": str(body.get("number", ""))[:8],
                        "to_go": bool(body.get("toGo")), **{k: _cents(body.get(k)) for k in ("subtotal", "discount", "tax", "total")}},
                  15 * 60)
    else:
        cache.delete(key)
    publish("popups")
    return JsonResponse({"ok": True})


@require_POST
@api_staff_required
def announce(request):
    """A staff announcement from the register ({"text"}): screens with the announcement pop-up show it."""
    from .popups import record
    try:
        text = str(json.loads(request.body).get("text", "")).strip()[:200]
    except (ValueError, AttributeError):
        text = ""
    if not text:
        return JsonResponse({"message": _("Type what to announce.")}, status=400)
    record("announcement", {"text": text, "by": request.user.first_name or request.user.username})
    return JsonResponse({"ok": True})
