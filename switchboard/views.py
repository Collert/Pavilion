"""
Switchboard's doors into Pavilion (protocol §1-§3):

- GET /.well-known/switchboard: the manifest, public.
- /switchboard/authorize: the consent page an admin with `admin.switchboard.edit` approves in their browser.
- /switchboard/v1/…: the JSON API Switchboard's server calls. No cookies, no CSRF token: the token call carries a
  one-time code, everything else the connection's key as a bearer token. These always answer JSON in the protocol's
  error shape, never a page or a redirect (users/setup.py and pavilion/throttle.py leave these paths alone; they keep
  their own flood cap here, answered as a 503 so Switchboard tries again).
"""
import json
import logging
from datetime import timedelta
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from pavilion import throttle

from . import actions, connections, manifest as manifest_data, options as options_data
from .models import IDEMPOTENCY_DAYS, IdempotencyRecord

logger = logging.getLogger(__name__)
CALLS, WINDOW = 300, 10  # calls per address per seconds: far more than workflows make, far less than a flood


def error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def api(*methods, auth=True):
    """A server-to-server JSON endpoint: capped, bearer-checked (the view gets the connection), and anything
    unexpected becomes a 500 `server_error` instead of an HTML error page."""
    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            try:
                ok, wait = throttle.allow("switchboard", throttle.address(request), CALLS, WINDOW)
                if not ok:
                    response = error(503, "server_error", "Pavilion is getting too many calls at once. Try again in a few seconds.")
                    response["Retry-After"] = str(wait)
                    return response
                if auth:
                    connection = connections.authenticate(request)
                    if connection is None:
                        return error(401, "unauthorized", "This Switchboard isn’t connected to Pavilion any more. Connect it again.")
                    args = (connection, *args)
                if request.method not in methods:
                    response = error(405, "invalid_request", f"Use {' or '.join(methods)} here.")
                    response["Allow"] = ", ".join(methods)
                    return response
                return view(request, *args, **kwargs)
            except Exception:
                logger.exception("Switchboard call failed: %s %s", request.method, request.path)
                return error(500, "server_error", "Something went wrong in Pavilion. Try again; if it keeps happening, tell whoever runs it.")
        return wrapper
    return decorator


def _body(request):
    """The request's JSON object, or None."""
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


# ---------- the manifest ----------

@api("GET", auth=False)
def manifest(request):
    response = JsonResponse(manifest_data.build(request))
    response["Cache-Control"] = "no-store"
    return response


# ---------- connecting ----------

def _with_query(url, **params) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in params] + list(params.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def authorize(request):
    """The consent page. Signed-out people sign in first and come back (LOGIN_URL's `next`); it takes Switchboard edit
    (users/access.py). Approve sends the browser back to Switchboard with a one-time code, Cancel with access_denied."""
    from admin_portal import switchboard as switchboard_page
    from admin_portal.views import page
    from pos_server.devices import no_access, staff_required
    from users.access import can, can_page
    user = request.user
    if not (user.is_authenticated and user.is_staff):
        return staff_required(lambda r: None)(request)  # off to sign in, then back here
    if not can(user, "app.admin") or not can_page(user, "switchboard", "edit"):
        return no_access(request, "app.admin")
    source = request.POST if request.method == "POST" else request.GET
    callback = connections.http_url(source.get("callback"))
    state, name = source.get("state", "")[:500], source.get("name", "").strip()[:120]
    if not callback:
        response = page(request, "switchboard/authorize.html", "switchboard", {"bad": True})
        response.status_code = 400
        return response
    if request.method == "POST":
        if request.POST.get("op") == "approve":
            code = connections.new_code(user, name, callback)
            return HttpResponseRedirect(_with_query(callback, code=code, state=state))
        return HttpResponseRedirect(_with_query(callback, error="access_denied", state=state))
    return page(request, "switchboard/authorize.html", "switchboard", {
        "callback": callback, "state": state, "name": name, "host": urlsplit(callback).netloc, "instance": connections.instance_name(),
        "allows": switchboard_page.allows()})


@api("POST", auth=False)
def token(request):
    body = _body(request)
    if body is None:
        return error(400, "invalid_request", "The request must be a JSON object.")
    webhook_url = connections.http_url(body.get("webhook_url"))
    if not webhook_url:
        return error(422, "invalid_params", "webhook_url must be an http or https address.")
    try:
        connection, key, secret = connections.redeem(body.get("code"), webhook_url, body.get("name"),
                                                     base_url=request.build_absolute_uri("/").rstrip("/"))
    except connections.InvalidCode:
        return error(400, "invalid_code", "This code is wrong, was already used or has expired. Start connecting again from Switchboard.")
    return JsonResponse({"api_key": key, "webhook_secret": secret, "connection_id": str(connection.pk),
                         "building": connections.BUILDING, "instance": connections.instance_name()})


# ---------- the API ----------

@api("GET")
def ping(request, connection):
    return JsonResponse({"ok": True, "building": connections.BUILDING, "instance": connections.instance_name(),
                         "connection_id": str(connection.pk)})


@api("GET")
def options(request, connection, name):
    """A list a param's `options` names (options.py), for Switchboard's builder to offer as a dropdown."""
    found = options_data.get(name)
    if found is None:
        return error(404, "unknown_options", f"Pavilion has no list called “{name}”.")
    return JsonResponse({"options": found})


@api("DELETE")
def connection_view(request, connection):
    connections.disconnect(connection)
    return JsonResponse({"ok": True})


@api("GET", "POST", "DELETE", "PUT", "PATCH")
def unknown(request, connection, rest=""):
    return error(404, "unknown_action", "Pavilion has nothing at this address.")


@api("POST")
def action(request, connection, name):
    spec = manifest_data.BY_NAME.get(name)
    if spec is None:
        return error(404, "unknown_action", f"Pavilion has no action called “{name}”.")
    body = _body(request)
    if body is None:
        return error(400, "invalid_request", "The request must be a JSON object of the action’s params.")
    key = request.headers.get("Idempotency-Key", "").strip()
    if len(key) > 200:
        return error(400, "invalid_request", "The Idempotency-Key can be up to 200 characters.")
    call = actions.Call(connection=connection, request=request)
    if spec["kind"] != "action" or not key:  # lookups may ignore the key
        status, answer = _run(spec, call, body)
        return JsonResponse(answer, status=status)
    with transaction.atomic():
        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(connection=connection, key=key, action=name)
        except IntegrityError:  # seen before: the answer given then, without doing the work again
            record = IdempotencyRecord.objects.get(connection=connection, key=key)
            return JsonResponse(record.body, status=record.status or 200)
        status, answer = _run(spec, call, body)  # anything unexpected rolls the record back too, so a retry runs it
        record.status, record.body = status, answer
        record.save(update_fields=["status", "body"])
    IdempotencyRecord.objects.filter(created_at__lt=timezone.now() - timedelta(days=IDEMPOTENCY_DAYS)).delete()
    return JsonResponse(answer, status=status)


def _run(spec, call, body) -> tuple[int, dict]:
    """(status, the JSON answer). The action's writes are all or nothing."""
    try:
        with transaction.atomic():
            return 200, actions.RUN[spec["name"]](call, actions.clean(spec, body))
    except actions.ActionError as e:
        return e.status, {"error": {"code": e.code, "message": e.message}}
