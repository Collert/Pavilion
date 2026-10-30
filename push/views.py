"""
What every installable app serves for push (the store's are online_store/app.py's, the courier app's deliveries/views.py's):
its service worker at the top of its scope, and the address that worker posts a renewed subscription to.
"""
import json

from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt

from . import subscriptions, vapid
from .models import PushSubscription


def service_worker(request, *, app, name, scope, renew, icon, badge="", offline="", precache=(), version="1"):
    """The service worker (push/templates/push/sw.js) for `app`, from the top of `scope`. Its text changes when the
    keys or the precached files do, which is how browsers know to update it."""
    config = {"app": app, "name": name, "scope": scope, "renew": renew, "icon": icon, "badge": badge,
              "offline": offline, "precache": list(precache), "key": vapid.public_key(), "version": version}
    body = render_to_string("push/sw.js", {"app": app, "scope": scope, "offline": offline,
                                           "config": json.dumps(config).replace("<", "\\u003c")})
    response = HttpResponse(body, content_type="text/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = scope
    return response


@csrf_exempt
def renew(request, app):
    """POST {subscription, replaces}: a service worker's renewed subscription. It takes over the old one's row (its
    account, topics and orders), so nothing here trusts the session: only someone who knew the old endpoint can."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        body = json.loads(request.body)
        fields = subscriptions.parse(body.get("subscription"))
    except (ValueError, AttributeError):
        return HttpResponseBadRequest("Expected a push subscription")
    old = PushSubscription.objects.filter(app=app, endpoint=str(body.get("replaces") or "")).first()
    if old is None:
        return JsonResponse({"saved": False})
    PushSubscription.objects.filter(endpoint=fields["endpoint"]).exclude(pk=old.pk).delete()
    old.endpoint, old.p256dh, old.auth, old.failures = fields["endpoint"], fields["p256dh"], fields["auth"], 0
    old.save()
    return JsonResponse({"saved": True})
