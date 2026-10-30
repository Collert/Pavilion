"""
listmonk (https://listmonk.app), which sends Pavilion's email: the connection (the admin's Email page, kept on
StoreSettings; the LISTMONK_* settings fill in when it's empty), putting Pavilion's own templates in it, and the
customers list for the newsletter.

`install` renders every email (misc_tools/emails.py) once per language the store has on, from
misc_tools/templates/emails/listmonk/<key>.html under that language (misc_tools/email_templates.py), creates them as
listmonk transactional templates, and remembers which is which (misc_tools.models.EmailTemplate). It also makes a
campaign template per language for newsletters, and one campaign template (every language in it) per marketing email. It only adds what's missing (or gone from listmonk); `overwrite`
rewrites the ones Pavilion made (Reset to default).

People (below): everyone with an account is a subscriber in the users list; the customers list is the newsletter's.
They're synced when listmonk is connected and whenever an account, its menu-email choice or its language changes.

Marketing emails (events) are campaigns: `start_campaign` makes one and starts it.

The listmonk API user needs a role that can: get and manage templates, send transactional messages, get and manage
lists, get and manage subscribers, and get and manage campaigns.
"""
import json
import logging
import threading
import urllib.error
import urllib.request

from django.conf import settings
from django.db import close_old_connections, transaction

logger = logging.getLogger(__name__)
TIMEOUT = 15


class ListmonkError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ---------- the connection ----------

def config(store=None) -> dict:
    from admin_portal.models import StoreSettings
    store = store or StoreSettings.load()
    return {"url": (store.listmonk_url or getattr(settings, "LISTMONK_URL", "")).rstrip("/"),
            "user": store.listmonk_user or getattr(settings, "LISTMONK_API_USER", ""),
            "token": store.listmonk_token or getattr(settings, "LISTMONK_API_TOKEN", ""),
            "from": store.listmonk_from or getattr(settings, "LISTMONK_FROM_EMAIL", "")}


def is_configured(cfg=None) -> bool:
    cfg = cfg or config()
    return bool(cfg["url"] and cfg["user"] and cfg["token"])


def api(method, path, payload=None, cfg=None):
    """Call the listmonk API and return the response's `data`."""
    cfg = cfg or config()
    if not is_configured(cfg):
        raise ListmonkError("listmonk isn’t connected.")
    request = urllib.request.Request(
        f"{cfg['url']}/api{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"token {cfg['user']}:{cfg['token']}", "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "Pavilion/1.0 (listmonk)"},  # Cloudflare rejects urllib's default agent
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except (ValueError, AttributeError):
            pass
        raise ListmonkError(f"listmonk returned {exc.code}: {detail}"[:300], exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ListmonkError(f"listmonk can’t be reached at {cfg['url']}: {getattr(exc, 'reason', exc)}") from exc
    return json.loads(body).get("data") if body else None


def _results(data):
    return data.get("results", []) if isinstance(data, dict) else (data or [])


def check(cfg) -> str:
    """Why these details don't work, or "" when they do (they can read lists and templates)."""
    try:
        api("GET", "/lists?minimal=true&per_page=all", cfg=cfg)
        api("GET", "/templates?no_body=true", cfg=cfg)
    except ListmonkError as e:
        if e.status in (401, 403):
            return f"listmonk said the API user or key is wrong, or its role can’t do this ({e.status}). Check both and try again."
        return str(e)
    return ""


def templates(cfg=None) -> dict:
    """{"tx": [(id, name)], "campaign": [(id, name)]} in listmonk."""
    out = {"tx": [], "campaign": []}
    for row in _results(api("GET", "/templates?no_body=true", cfg=cfg)):
        if row.get("type") in out:
            out[row["type"]].append((row["id"], row.get("name", "")))
    return out


# ---------- Pavilion's templates ----------

def languages() -> list:
    return [code for code, _name in settings.LANGUAGES]


def install(keys=None, overwrite=False) -> dict:
    """Put Pavilion's templates in listmonk for every language that's on: {"made", "updated", "kept"}."""
    from . import email_templates
    from .emails import KINDS
    from .models import EmailTemplate
    have = {tid for rows in templates().values() for tid, _name in rows}
    done = {"made": 0, "updated": 0, "kept": 0}
    jobs = [(k.key, email_templates.render_campaign if k.campaign else email_templates.render) for k in KINDS]
    jobs.append(("newsletter", email_templates.render_newsletter))
    for key, render in jobs:
        if keys and key not in keys:
            continue
        campaign = render is email_templates.render_campaign
        for lang in ([EmailTemplate.ALL] if campaign else languages()):
            row = EmailTemplate.objects.filter(kind=key, language=lang).first()
            there = row is not None and row.template_id in have
            if there and not (overwrite and row.managed):
                done["kept"] += 1
                continue
            payload = render(key) if campaign else render(key, lang) if key != "newsletter" else render(lang)
            if there:
                api("PUT", f"/templates/{row.template_id}", payload)
                done["updated"] += 1
            else:
                made = api("POST", "/templates", payload)["id"]
                EmailTemplate.objects.update_or_create(kind=key, language=lang, defaults={"template_id": made, "managed": True})
                done["made"] += 1
    if not keys:
        ensure_lists()
    return done


def missing() -> int:
    """How many (email, language) pairs have no template in listmonk."""
    from .emails import KINDS
    from .models import EmailTemplate
    have = set(EmailTemplate.objects.values_list("kind", "language"))
    return sum(1 for k in KINDS for lang in template_languages(k) if (k.key, lang) not in have)


def template_languages(kind) -> list:
    """What an email has a template for: each language that's on, or all of them at once for a marketing email."""
    from .models import EmailTemplate
    return [EmailTemplate.ALL] if kind.campaign else languages()


# ---------- campaigns (marketing email) ----------

def all_lists(cfg=None) -> list:
    """[{"id", "name", "count"}] every list in listmonk."""
    return [{"id": row["id"], "name": row.get("name", ""), "count": row.get("subscriber_count")}
            for row in _results(api("GET", "/lists?minimal=true&per_page=all", cfg=cfg))]


def start_campaign(name, subject, body, template_id, list_ids) -> int:
    """Make a campaign of Pavilion's content for these lists and send it now. Returns its id."""
    payload = {"name": name[:200], "subject": subject, "lists": list_ids, "type": "regular", "content_type": "html",
               "body": body, "template_id": template_id, "messenger": "email", "tags": ["pavilion"]}
    sender = config()["from"]
    if sender:
        payload["from_email"] = sender
    made = api("POST", "/campaigns", payload)["id"]
    api("PUT", f"/campaigns/{made}/status", {"status": "running"})
    return made


# ---------- people: the users list and the customers list ----------
#
# Everyone with an account (staff and customers) is a listmonk subscriber in the "<store> users" list, so
# transactional email always finds them. That list is private and never gets campaigns, so no unsubscribe link ever
# reaches it (and listmonk sends transactional email even to someone who blocklisted themselves). People who ticked
# "Monthly menu email" are also in "<store> customers", the list newsletters go to, which they can leave.

SYNC = "listmonk-sync"   # cache: {"state": "running"|"done"|"failed", "count", "error"}

LISTS = {
    "users": ("listmonk_users_list", "{store} users",
              "Everyone with an account at {store} (Pavilion keeps it up to date). For transactional email only: "
              "never send it a campaign, so nobody can unsubscribe from receipts and password resets."),
    "customers": ("listmonk_list", "{store} customers",
                  "Customers who asked for the menu email in their account (Pavilion keeps it up to date)."),
}


def ensure_lists() -> dict:
    """{"users": id, "customers": id}: the two lists, made once (or found again by name)."""
    from admin_portal.models import StoreSettings
    store = StoreSettings.load()
    have = {row["id"]: row for row in _results(api("GET", "/lists?minimal=true&per_page=all"))}
    out = {}
    for key, (field, name, about) in LISTS.items():
        current = getattr(store, field)
        if current in have:
            out[key] = current
            continue
        name = name.format(store=store.name)
        found = next((i for i, row in have.items() if row.get("name") == name), None)
        if found is None:
            found = api("POST", "/lists", {"name": name, "type": "private", "optin": "single", "tags": ["pavilion"],
                                           "description": about.format(store=store.name)})["id"]
        StoreSettings.objects.filter(pk=store.pk).update(**{field: found})
        StoreSettings.forget()  # update() skips save(), which clears the cached row
        out[key] = found
    return out


def ensure_list() -> int:
    return ensure_lists()["customers"]


def _subscriber(email):
    query = "LOWER(subscribers.email)=LOWER('%s')" % email.replace("'", "")
    rows = _results(api("GET", "/subscribers?per_page=5&query=" + urllib.request.quote(query)))
    return rows[0] if rows else None


def add_address(email):
    """A subscriber for an address with no account (a gift card's recipient, a receipt staff typed in), in no list:
    older listmonk only sends transactional email to subscribers."""
    if _subscriber(email) is None:
        api("POST", "/subscribers", {"email": email, "name": email.split("@")[0], "status": "enabled", "lists": [],
                                     "attribs": {"pavilion": "address"}, "preconfirm_subscriptions": True})


def _listed(found, list_id) -> bool:
    return any(l.get("id") == list_id and l.get("subscription_status") != "unsubscribed" for l in found.get("lists") or [])


def sync_user(user, lists=None):
    """Put one person in listmonk: the users list always, the customers list while they want the menu email (with
    their language). Someone who left the customers list in listmonk is only put back if they tick it again here."""
    from users.models import language_of
    if not user.email or not user.is_active:
        return
    lists = lists or ensure_lists()
    wants = bool(getattr(getattr(user, "customer_profile", None), "newsletter", False))
    attribs = {"pavilion_id": user.pk, "language": language_of(user), "staff": bool(user.is_staff)}
    name = user.get_full_name() or user.username
    found = _subscriber(user.email)
    if found is None:
        api("POST", "/subscribers", {"email": user.email, "name": name, "status": "enabled", "attribs": attribs,
                                     "lists": [lists["users"]] + ([lists["customers"]] if wants else []),
                                     "preconfirm_subscriptions": True})
        return
    if not _listed(found, lists["users"]):
        api("PUT", "/subscribers/lists", {"ids": [found["id"]], "action": "add", "target_list_ids": [lists["users"]], "status": "confirmed"})
    if wants and not _listed(found, lists["customers"]):
        api("PUT", "/subscribers/lists", {"ids": [found["id"]], "action": "add", "target_list_ids": [lists["customers"]], "status": "confirmed"})
    elif not wants and _listed(found, lists["customers"]):
        api("PUT", "/subscribers/lists", {"ids": [found["id"]], "action": "unsubscribe", "target_list_ids": [lists["customers"]]})
    merged = {**(found.get("attribs") or {}), **attribs}
    if found.get("name") != name or merged != (found.get("attribs") or {}):
        api("PATCH", f"/subscribers/{found['id']}", {"name": name, "attribs": merged})


def sync_customer(user):  # the name the newsletter switch used
    sync_user(user)


def people():
    from django.contrib.auth.models import User
    return User.objects.filter(is_active=True).exclude(email="").select_related("customer_profile", "preference")


def sync_all() -> int:
    """Everyone with an email into listmonk. Returns how many."""
    lists = ensure_lists()
    count = 0
    for user in people():
        sync_user(user, lists)
        count += 1
    return count


def _background(job):
    def run():
        close_old_connections()
        try:
            job()
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()


def sync_all_later():
    """Sync everyone in the background (after connecting); the Email page shows how it's going (SYNC)."""
    from django.core.cache import cache
    cache.set(SYNC, {"state": "running", "count": 0, "error": ""}, 60 * 60)

    def job():
        try:
            cache.set(SYNC, {"state": "done", "count": sync_all(), "error": ""}, 60 * 60)
        except ListmonkError as e:
            cache.set(SYNC, {"state": "failed", "count": 0, "error": str(e)}, 60 * 60)

    transaction.on_commit(lambda: _background(job))


def sync_later(user):
    """After this transaction, in the background: sync someone whose account, menu-email choice or language changed."""
    if not is_configured():
        return

    def job():
        try:
            sync_user(user)
        except ListmonkError:
            logger.warning("Couldn't sync %s with listmonk", user.pk, exc_info=True)

    transaction.on_commit(lambda: _background(job))
