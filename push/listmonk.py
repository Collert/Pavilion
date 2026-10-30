"""
Push campaigns from listmonk: Pavilion is a listmonk messenger (https://listmonk.app/docs/messengers/). A campaign set
to the "pavilion-push" messenger is posted here one message at a time, and each goes out as a push notification to that
person's store devices that turned on "News and offers" (PushSubscription.news_updates).

How they hook up: every account is a listmonk subscriber (misc_tools/listmonk.py: sync_user), whose attributes carry
`pavilion_id` and `pavilion_push`, the ids of their devices with news on (kept in step whenever one is added, removed or
switched). The messenger reads both and sends only to devices of that account that still want news. Guests have no
subscriber, so campaigns only reach people with an account.

The message: the campaign's subject is the notification's title and its body the text (HTML is turned into plain text;
plain text is what listmonk suggests for messengers). The first link in the body is where a tap goes (with @TrackLink
it's listmonk's tracking address, so clicks count) and is left out of the text; without one the store opens.

Setting up (the admin's Notifications page): `install` adds or updates the messenger in listmonk's settings (listmonk
reloads itself) with Basic auth credentials Pavilion made (user "pavilion", a random password kept encrypted), and
a "Pavilion push" campaign template (plain text, just the content).
"""
import base64
import hmac
import html
import json
import logging
import re
import secrets as tokens

from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.decorators.csrf import csrf_exempt

from payments import secrets

logger = logging.getLogger(__name__)
NAME = "pavilion-push"  # the messenger's name in listmonk (lowercase letters, digits and dashes)
USER = "pavilion"
# The subscriber attribute listing a person's devices with news on. Named for Pavilion, like pavilion_id: other
# apps sharing the listmonk mustn't be able to reach these devices with an attribute of their own
ATTRIBUTE = "pavilion_push"
TEMPLATE_NAME = "Pavilion push"
TEMPLATE_BODY = '{{ template "content" . }}'
MASK = "•"
LINK = re.compile(r"https?://[^\s<>\"']+")


# ---------- the credentials listmonk signs in with ----------

def password(store=None, make=True) -> str:
    """The messenger's password, made the first time it's needed (`make`)."""
    from admin_portal.models import StoreSettings
    store = store or StoreSettings.load()
    current = secrets.decrypt(store.push_messenger_password)
    if current or not make:
        return current
    current = tokens.token_urlsafe(24)
    store.push_messenger_password = secrets.encrypt(current)
    store.save(update_fields=["push_messenger_password"])
    return current


def new_password() -> str:
    from admin_portal.models import StoreSettings
    store = StoreSettings.load()
    store.push_messenger_password = ""
    store.save(update_fields=["push_messenger_password"])
    return password(store)


def default_url(request) -> str:
    return request.build_absolute_uri(reverse("listmonk_messenger"))


def messenger_url(request=None) -> str:
    from admin_portal.models import StoreSettings
    return StoreSettings.load().push_messenger_url or (default_url(request) if request else "")


def signed_in(request) -> bool:
    expected = password(make=False)
    header = request.headers.get("Authorization", "")
    if not expected or not header.startswith("Basic "):
        return False
    try:
        user, _sep, given = base64.b64decode(header[6:]).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(user.encode(), USER.encode()) & hmac.compare_digest(given.encode(), expected.encode())


# ---------- in listmonk ----------

def _unmask(value):
    """listmonk shows saved passwords as dots; sent back empty, it keeps them."""
    if isinstance(value, dict):
        return {k: _unmask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unmask(v) for v in value]
    if isinstance(value, str) and value and set(value) == {MASK}:
        return ""
    return value


STATUS = "push-listmonk-status"


def status(force=False) -> dict:
    """{"messenger": bool, "enabled": bool, "url": str, "template": bool, "error": str} as listmonk has them (kept a
    minute, so the Notifications page stays quick)."""
    from django.core.cache import cache
    from misc_tools import listmonk
    from admin_portal.models import StoreSettings
    out = {"messenger": False, "enabled": False, "url": "", "template": False, "error": ""}
    if not listmonk.is_configured():
        return out
    cached = None if force else cache.get(STATUS)
    if cached:
        return cached
    try:
        found = next((m for m in listmonk.api("GET", "/settings").get("messengers") or [] if m.get("name") == NAME), None)
        template = StoreSettings.load().push_messenger_template
        templates = listmonk.templates()
    except listmonk.ListmonkError as e:
        out["error"] = str(e)
        return out
    if found:
        out.update(messenger=True, enabled=bool(found.get("enabled")), url=found.get("root_url", ""))
    out["template"] = any(tid == template for tid, _name in templates["campaign"])
    cache.set(STATUS, out, 60)
    return out


def install(url) -> dict:
    """Add (or bring up to date) the messenger and the campaign template in listmonk. Returns status()."""
    from misc_tools import listmonk
    from admin_portal.models import StoreSettings
    entry = {"enabled": True, "name": NAME, "root_url": url, "username": USER, "password": password(),
             "max_conns": 4, "timeout": "10s", "max_msg_retries": 2}
    current = _unmask(listmonk.api("GET", "/settings"))
    messengers = current.get("messengers") or []
    mine = next((m for m in messengers if m.get("name") == NAME), None)
    if mine:
        mine.update(entry)
    else:
        messengers.append(entry)
    current["messengers"] = messengers
    listmonk.api("PUT", "/settings", current)  # listmonk reloads itself (or after its running campaigns)

    store = StoreSettings.load()
    have = {tid for tid, _name in listmonk.templates()["campaign"]}
    if store.push_messenger_template not in have:
        made = listmonk.api("POST", "/templates", {"name": TEMPLATE_NAME, "type": "campaign", "body": TEMPLATE_BODY})
        store.push_messenger_template = made["id"]
    store.push_messenger_url = url
    store.save(update_fields=["push_messenger_template", "push_messenger_url"])
    from django.core.cache import cache
    out = {"messenger": True, "enabled": True, "url": url, "template": True, "error": ""}
    cache.set(STATUS, out, 60)
    return out


# ---------- the messages ----------

def message(subject, body, content_type, campaign) -> dict:
    """The notification for one message of a campaign."""
    text = body or ""
    if content_type in ("html", "richtext", "visual"):
        text = html.unescape(strip_tags(re.sub(r"<(br|/p|/div|/h\d|/li)\b[^>]*>", "\n", text, flags=re.I)))
    link = LINK.search(text)
    url = link.group(0).rstrip(".,)") if link else reverse("store_home")
    if link:
        text = text.replace(link.group(0), "")
    text = re.sub(r"\s+", " ", text).strip()
    title = re.sub(r"\s+", " ", subject or "").strip()
    return {"title": title[:120], "body": text[:300], "url": url,
            "tag": f"campaign-{(campaign or {}).get('uuid', '')}"[:64]}


def devices(recipient):
    """The store devices one recipient's notification goes to: that account's, with news on, listed in its attributes."""
    from .models import PushSubscription
    attribs = recipient.get("attribs") if isinstance(recipient, dict) else None
    attribs = attribs if isinstance(attribs, dict) else {}
    account, listed = attribs.get("pavilion_id"), attribs.get(ATTRIBUTE)
    if not isinstance(account, int) or not isinstance(listed, list):
        return PushSubscription.objects.none()
    ids = [i for i in listed if isinstance(i, int)][:50]
    return PushSubscription.objects.filter(pk__in=ids, user_id=account, app=PushSubscription.STORE, news_updates=True)


@csrf_exempt
def receive(request):
    """POST from listmonk: one campaign message (its postback payload). 200 means taken (listmonk retries anything
    else), so a recipient with no devices is still a 200."""
    from . import kinds, send
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not signed_in(request):
        response = HttpResponse("Sign in with the messenger's user and password.", status=401)
        response["WWW-Authenticate"] = 'Basic realm="Pavilion push"'
        return response
    try:
        payload = json.loads(request.body)
        recipients = payload.get("recipients") or []
    except (ValueError, AttributeError):
        return HttpResponseBadRequest("Expected listmonk's postback JSON")
    if not kinds.enabled("campaigns"):
        return JsonResponse({"devices": 0, "off": True})
    built = message(payload.get("subject"), payload.get("body"), payload.get("content_type"), payload.get("campaign"))
    subs = [sub for recipient in recipients[:100] for sub in devices(recipient)]
    if subs:
        send.pooled(lambda: send.send_now(subs, lambda: built))
    return JsonResponse({"devices": len(subs)})
