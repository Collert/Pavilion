"""
listmonk (https://listmonk.app), which sends Pavilion's email: the connection (the admin's Email page, kept on
StoreSettings; the LISTMONK_* settings fill in when it's empty), putting Pavilion's own templates in it, and the
customers list for the newsletter.

`install` renders every email (misc_tools/emails.py) once per language the store has on, from
misc_tools/templates/emails/listmonk/<key>.html under that language (misc_tools/email_templates.py), creates them as
listmonk transactional templates, and remembers which is which (misc_tools.models.EmailTemplate). It also makes a
campaign template per language for newsletters, and the customers list. It only adds what's missing (or gone from
listmonk); `overwrite` rewrites the ones Pavilion made (Reset to default).

The listmonk API user needs a role that can: get and manage templates, send transactional messages, get and manage
lists, and get and manage subscribers.
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
    jobs = [(k.key, email_templates.render) for k in KINDS] + [("newsletter", email_templates.render_newsletter)]
    for key, render in jobs:
        if keys and key not in keys:
            continue
        for lang in languages():
            row = EmailTemplate.objects.filter(kind=key, language=lang).first()
            there = row is not None and row.template_id in have
            if there and not (overwrite and row.managed):
                done["kept"] += 1
                continue
            payload = render(key, lang) if key != "newsletter" else render(lang)
            if there:
                api("PUT", f"/templates/{row.template_id}", payload)
                done["updated"] += 1
            else:
                made = api("POST", "/templates", payload)["id"]
                EmailTemplate.objects.update_or_create(kind=key, language=lang, defaults={"template_id": made, "managed": True})
                done["made"] += 1
    if not keys:
        ensure_list()
    return done


def missing() -> int:
    """How many (email, language) pairs have no template in listmonk."""
    from .emails import KINDS
    from .models import EmailTemplate
    have = set(EmailTemplate.objects.values_list("kind", "language"))
    return sum(1 for k in KINDS for lang in languages() if (k.key, lang) not in have)


# ---------- the newsletter list ----------

def ensure_list() -> int:
    """The "<store> customers" list (made once), for newsletters written in listmonk."""
    from admin_portal.models import StoreSettings
    store = StoreSettings.load()
    lists = {row["id"]: row for row in _results(api("GET", "/lists?minimal=true&per_page=all"))}
    if store.listmonk_list in lists:
        return store.listmonk_list
    name = f"{store.name} customers"
    found = next((i for i, row in lists.items() if row.get("name") == name), None)
    if found is None:
        found = api("POST", "/lists", {"name": name, "type": "private", "optin": "single", "tags": ["pavilion"],
                                       "description": "Customers who asked for the menu email in their account (Pavilion keeps it up to date)."})["id"]
    StoreSettings.objects.filter(pk=store.pk).update(listmonk_list=found)
    return found


def _subscriber(email):
    rows = _results(api("GET", "/subscribers?per_page=5&query=" + urllib.request.quote(f"LOWER(subscribers.email)=LOWER('{email.replace(chr(39), '')}')")))
    return rows[0] if rows else None


def sync_customer(user):
    """Put a customer in the newsletter list when they want the menu email (with their language), take them out
    when they don't."""
    from admin_portal.models import StoreSettings
    from users.models import language_of
    list_id = StoreSettings.load().listmonk_list
    if not list_id or not user.email:
        return
    wants = bool(getattr(getattr(user, "customer_profile", None), "newsletter", False))
    found = _subscriber(user.email)
    attribs = {"pavilion_id": user.pk, "language": language_of(user)}
    if found is None:
        if not wants:
            return
        api("POST", "/subscribers", {"email": user.email, "name": user.get_full_name() or user.username, "status": "enabled",
                                     "lists": [list_id], "attribs": attribs, "preconfirm_subscriptions": True})
        return
    api("PUT", "/subscribers/lists", {"ids": [found["id"]], "action": "add" if wants else "remove", "target_list_ids": [list_id],
                                      **({"status": "confirmed"} if wants else {})})
    if wants:
        api("PATCH", f"/subscribers/{found['id']}", {"attribs": {**(found.get("attribs") or {}), **attribs}})


def sync_all() -> int:
    from django.contrib.auth.models import User
    count = 0
    for user in User.objects.filter(customer_profile__isnull=False).exclude(email="").select_related("customer_profile"):
        sync_customer(user)
        count += 1
    return count


def sync_later(user):
    """After this transaction, in the background: sync a customer whose newsletter choice or language changed."""
    if not is_configured():
        return

    def run():
        close_old_connections()
        try:
            sync_customer(user)
        except ListmonkError:
            logger.warning("Couldn't sync %s with listmonk", user.pk, exc_info=True)
        finally:
            close_old_connections()

    transaction.on_commit(lambda: threading.Thread(target=run, daemon=True).start())
