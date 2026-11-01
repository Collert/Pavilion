import json

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from pavilion import throttle

from . import pairing

PAIR_PER_MINUTE = 10  # tries per address: six digits that last 15 minutes can't be guessed at this rate
PAIR_ALL_PER_MINUTE = 60  # and for everyone together


@csrf_exempt  # a dongle, not a browser: no session, no cookies
@require_POST
def pair(request):
    """
    POST /dongles/pair {"code", "hardware_id", "model", "version", "resolution", "network": {...}} from a dongle in
    setup. 200 {"token", "dongle": {"id", "name"}, "store": {"name"}}; else {"error": kind, "message"} with 400
    (bad_request), 404 (bad_code), 410 (expired, used) or 429 (throttled).
    """
    for scope, who, limit in (("dongle-pair", throttle.address(request), PAIR_PER_MINUTE), ("dongle-pair-all", "all", PAIR_ALL_PER_MINUTE)):
        ok, wait = throttle.allow(scope, who, limit, 60)
        if not ok:
            response = JsonResponse({"error": "throttled", "message": _("Too many tries. Wait a minute and try again."), "retry_after": wait}, status=429)
            response["Retry-After"] = str(wait)
            return response
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "bad_request", "message": _("The dongle sent an incomplete request.")}, status=400)
    try:
        dongle, token = pairing.pair(body.get("code"), body)
    except pairing.PairError as error:
        status = {"bad_request": 400, "bad_code": 404}.get(error.kind, 410)
        return JsonResponse({"error": error.kind, "message": error.message}, status=status)
    from admin_portal.models import StoreSettings
    return JsonResponse({"token": token, "dongle": {"id": dongle.pk, "name": dongle.name},
                         "store": {"name": StoreSettings.load().name}})


def open_screen(request, value):
    """
    A dongle's launch link (pairing.launch_path), what its browser opens: leaves the dongle's cookie, which lets this
    browser see the dongle's screen (screens.views), then shows it.
    """
    dongle = pairing.read_launch(value)
    if dongle is None:
        return HttpResponse(_("This dongle isn’t paired with Pavilion any more."), status=403, content_type="text/plain; charset=utf-8")
    if dongle.screen_id is None:
        return HttpResponse(_("No screen is assigned to this dongle."), status=404, content_type="text/plain; charset=utf-8")
    response = redirect("screen", slug=dongle.screen.slug)
    return pairing.set_cookie(response, dongle, secure=request.is_secure() or not settings.DEBUG)
