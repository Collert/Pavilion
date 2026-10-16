import time

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import get_object_or_404, render

from pos_server.devices import staff_required

from . import registry
from .layout import render_screen
from .models import Screen


@staff_required
def index(request):
    """Every screen, plus the widget catalogue to build them from."""
    return render(request, "screens/index.html", {
        "screens": Screen.objects.all(),
        "widgets": registry.widgets().values(),
    })


def show(request, slug):
    """
    A screen, rendered from its layout on the server. static/js/live-page.js keeps it current: it refetches this page
    on the live widgets' SSE topics (and "screens", when someone edits a layout) and swaps in the widgets that changed.
    """
    screen = get_object_or_404(Screen, slug=slug)
    if not screen.public and not (request.user.is_authenticated and request.user.is_staff):
        return redirect_to_login(request.get_full_path())
    rendered = render_screen(screen, request)
    return render(request, "screens/screen.html", {
        "screen": screen,
        "rendered": rendered,
        "topics": ["screens", *rendered.topics],
        "now_ms": int(time.time() * 1000),
    })
