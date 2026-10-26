"""
Email (design: AdminEmail): connecting listmonk, which sends Pavilion's email (misc_tools/listmonk.py), and every
email Pavilion sends (misc_tools/emails.py) by group: on or off, and the listmonk template it uses in each language
that's on. Pavilion's own templates are put in listmonk with "Add Pavilion’s templates" (one per email and
language, plus a newsletter campaign template and the customers list); "Reset to default" writes one email's again.
Connecting, disconnecting, installing, resetting, syncing and test emails happen straight away; the switches and
template picks are a draft saved with Save changes.

    request.session["admin_email"] = {"on": {kind: bool}, "tpl": {"kind:lang": id or ""}, "cat", "open": [kinds],
        "url", "user", "from", "show"}
"""
import re

from django.core.cache import cache
from django.utils.translation import gettext as _

from misc_tools import listmonk
from misc_tools.emails import BY_KEY, GROUPS, KINDS
from misc_tools.models import EmailSwitch, EmailTemplate

from .languages import native

KEY = "admin_email"
CACHE = "admin-email-templates"
URL = re.compile(r"^https?://[^\s/]+\.[^\s]+$|^https?://localhost(:\d+)?(/.*)?$")


def listed():
    return [k for k in KINDS if not k.hidden]


def saved() -> dict:
    switches = dict(EmailSwitch.objects.values_list("kind", "enabled"))
    picks = {f"{k}:{lang}": tid for k, lang, tid in EmailTemplate.objects.values_list("kind", "language", "template_id")}
    return {"on": {k.key: (True if k.always else switches.get(k.key, k.default_on)) for k in listed()},
            "tpl": {f"{k.key}:{lang}": picks.get(f"{k.key}:{lang}", "") for k in listed() for lang in listmonk.languages()}}


def state(session, store) -> dict:
    st = session.get(KEY)
    if not st:
        cfg = listmonk.config(store)
        st = {**saved(), "cat": GROUPS[0][0], "open": [], "url": cfg["url"], "user": cfg["user"], "from": cfg["from"], "show": False}
    return st


def take_post(st, post):
    for f, size in (("url", 200), ("user", 80), ("from", 160)):
        if f in post:
            st[f] = post.get(f, "").strip()[:size]
    for key in list(st["tpl"]):
        field = "tpl_" + key.replace(":", "_")
        if field in post:
            value = post.get(field, "")
            st["tpl"][key] = int(value) if value.isdigit() else ""


def listmonk_templates(force=False):
    """(templates by type, error): listmonk's templates, kept a minute so the page stays quick."""
    if not listmonk.is_configured():
        return {"tx": [], "campaign": []}, ""
    found = None if force else cache.get(CACHE)
    if found is None:
        try:
            found = listmonk.templates()
        except listmonk.ListmonkError as e:
            return {"tx": [], "campaign": []}, str(e)
        cache.set(CACHE, found, 60)
    return found, ""


def apply_op(st, op, request, store) -> tuple[str, str]:
    """(toast, error) for one button."""
    name, _sep, arg = op.partition(":")
    if name == "cat" and arg in dict((g[0], 1) for g in GROUPS):
        st["cat"] = arg
    elif name == "flip" and arg in BY_KEY and not BY_KEY[arg].always:
        st["on"][arg] = not st["on"].get(arg, True)
    elif name == "data" and arg in BY_KEY:
        st["open"] = [k for k in st["open"] if k != arg] if arg in st["open"] else st["open"] + [arg]
    elif name == "showkey":
        st["show"] = not st["show"]
    elif name == "connect":
        token = request.POST.get("token", "").strip()
        cfg = {"url": st["url"].rstrip("/"), "user": st["user"], "token": token or store.listmonk_token, "from": st["from"]}
        if not URL.match(cfg["url"]) or not cfg["user"] or not cfg["token"]:
            return "", _("Fill in the address, the API user and its key.")
        problem = listmonk.check(cfg)
        if problem:
            return "", problem
        store.listmonk_url, store.listmonk_user, store.listmonk_token, store.listmonk_from = cfg["url"], cfg["user"], cfg["token"], cfg["from"]
        store.save(update_fields=["listmonk_url", "listmonk_user", "listmonk_token", "listmonk_from"])
        cache.delete(CACHE)
        found, _err = listmonk_templates(force=True)
        try:
            listmonk.ensure_lists()
        except listmonk.ListmonkError as e:
            return "", str(e)
        listmonk.sync_all_later()  # everyone with an account becomes a subscriber, in the background
        return _("Connected to listmonk. %(n)d templates found. Adding everyone’s accounts to listmonk now.") % {"n": len(found["tx"])}, ""
    elif name == "disconnect":
        store.listmonk_token = ""
        store.save(update_fields=["listmonk_token"])
        cache.delete(CACHE)
        return _("Disconnected. Email stops until you connect again."), ""
    elif name == "refresh":
        found, err = listmonk_templates(force=True)
        return ("", err) if err else (_("Templates reloaded from listmonk."), "")
    elif name == "install":
        try:
            done = listmonk.install()
        except listmonk.ListmonkError as e:
            return "", str(e)
        cache.delete(CACHE)
        st.update(saved())
        return _("Pavilion’s templates are in listmonk: %(made)d added, %(kept)d already there.") % done, ""
    elif name == "reset" and arg in BY_KEY:
        EmailTemplate.objects.filter(kind=arg, managed=False).delete()  # their own picks: make Pavilion's again
        try:
            listmonk.install(keys=[arg], overwrite=True)
        except listmonk.ListmonkError as e:
            return "", str(e)
        cache.delete(CACHE)
        st["tpl"].update({k: v for k, v in saved()["tpl"].items() if k.startswith(arg + ":")})
        return _("%(email)s is back to Pavilion’s template.") % {"email": BY_KEY[arg].label}, ""
    elif name == "test":
        from misc_tools.email import send
        to = request.user.email
        if not to:
            return "", _("Your account has no email address to send the test to.")
        try:
            send("test", to, {"name": request.user.first_name or request.user.username}, user=request.user)
        except listmonk.ListmonkError as e:
            return "", str(e)
        return _("Test email sent to %(email)s.") % {"email": to}, ""
    elif name == "sync":
        listmonk.sync_all_later()
        return _("Updating everyone in listmonk. This page shows when it’s done."), ""
    return "", ""


def save(st):
    for kind, on in st["on"].items():
        if kind in BY_KEY and not BY_KEY[kind].always:
            EmailSwitch.objects.update_or_create(kind=kind, defaults={"enabled": on})
    current = {f"{k}:{lang}": (tid, managed) for k, lang, tid, managed in EmailTemplate.objects.values_list("kind", "language", "template_id", "managed")}
    for key, tid in st["tpl"].items():
        kind, lang = key.split(":", 1)
        before = current.get(key)
        if not tid:
            EmailTemplate.objects.filter(kind=kind, language=lang).delete()
        elif not before or before[0] != tid:
            EmailTemplate.objects.update_or_create(kind=kind, language=lang, defaults={"template_id": tid, "managed": False})


# ---------- the page ----------

def page(st, store) -> dict:
    cfg = listmonk.config(store)
    connected = listmonk.is_configured(cfg)
    found, err = listmonk_templates()
    names = dict(found["tx"])
    dirty = {"on": st["on"], "tpl": st["tpl"]} != saved()
    langs = listmonk.languages()
    lang_names = {code: native(code) for code in langs}
    groups, missing_total = [], 0
    for key, label, icon in GROUPS:
        kinds = [k for k in listed() if k.group == key]
        on = sum(1 for k in kinds if st["on"].get(k.key))
        warn = any(st["on"].get(k.key) and not st["tpl"].get(f"{k.key}:{lang}") for k in kinds for lang in langs)
        groups.append({"key": key, "label": label, "icon": icon, "count": _("%(on)d of %(n)d on") % {"on": on, "n": len(kinds)},
                       "warn": warn, "on": st["cat"] == key})
    emails = []
    for k in listed():
        missing_total += sum(1 for lang in langs if st["on"].get(k.key) and not st["tpl"].get(f"{k.key}:{lang}"))
        if k.group != st["cat"]:
            continue
        rows = []
        for lang in langs:
            pick = st["tpl"].get(f"{k.key}:{lang}") or ""
            options = [(tid, name) for tid, name in found["tx"]]
            if pick and pick not in names:
                options.append((pick, _("#%(id)s (missing in listmonk)") % {"id": pick}))
            rows.append({"lang": lang, "name": lang_names.get(lang, lang), "field": f"tpl_{k.key}_{lang}", "pick": pick, "options": options,
                         "missing": not pick, "gone": bool(pick) and bool(names) and pick not in names})
        emails.append({"k": k, "on": st["on"].get(k.key, True), "rows": rows, "open": k.key in st["open"],
                       "vars": [("{{ .Tx.Data.%s }}" % f, str(d)) for f, d in k.fields],
                       "example": "Hi {{ .Tx.Data.%s }}," % (k.fields[0][0] if k.fields else "name")})
    group = next(g for g in groups if g["on"])
    return {
        "st": st, "connected": connected, "err": err, "dirty": dirty and connected, "groups": groups, "group": group, "emails": emails,
        "langs": [lang_names.get(lang, lang) for lang in langs], "missing": listmonk.missing() if connected else 0, "missing_picks": missing_total,
        "url_ok": bool(URL.match(st["url"])), "has_token": bool(cfg["token"]),
        "facts": [(_("Server"), cfg["url"].split("://", 1)[-1]), (_("API user"), cfg["user"]),
                  (_("Sends as"), cfg["from"] or _("listmonk’s default")), (_("Templates"), _("%(n)d transactional") % {"n": len(found["tx"])})],
        "edit_url": cfg["url"] + "/admin/campaigns/templates", "list_id": store.listmonk_list, "users_list": store.listmonk_users_list,
        "sync": cache.get(listmonk.SYNC),
        "teaser": [_("Order receipts"), _("Ready for pickup"), _("Catering updates"), _("Gift cards"), _("Password resets"), _("Schedules")],
    }
