"""
Groups (design: AdminGroups): what people can open and change (django.contrib.auth Group + users.GroupInfo; the codes
are users/access.py's). One form with Save: the list (members and how much each group grants), and the picked
group's name, description, apps, actions and admin pages (view, edit, delete per page; edit and delete bring view,
taking view away takes the rest). Owners are locked: they always have everything, only the description changes.
Members are added on the Staff page (or by a role's starting groups); this page only shows them.

    request.session["admin_groups"] = {"rows": [{"id": 3 or "n…", "name", "desc", "color", "icon", "owners": bool,
                                                 "perms": [codes]}], "sel": id, "gone": [ids], "q": "", "only": False}
"""
from django.contrib.auth.models import Group, User
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from users import access
from users.models import GroupInfo

from .team import GROUP_ICONS, face, full_name, initials

KEY = "admin_groups"
COLORS = ["#4E79B5", "#2BA3A0", "#E87BA4", "#6B7A2E", "#5B6A62", "#C9821A", "#7A5AC8", "#12875A"]


def saved() -> list:
    for group in Group.objects.filter(info__isnull=True):  # made elsewhere (Django admin): give it a look
        GroupInfo.objects.create(group=group, position=Group.objects.count())
    rows = []
    for info in GroupInfo.objects.select_related("group").order_by("position", "id"):
        rows.append({"id": info.group_id, "name": info.group.name, "desc": info.description, "color": info.color,
                     "icon": info.icon if info.icon in GROUP_ICONS else "people", "owners": info.owners,
                     "perms": [] if info.owners else access.tidy(info.perms or [])})
    return rows


def state(session) -> dict:
    st = session.get(KEY)
    if not st:
        rows = saved()
        front = next((r for r in rows if not r["owners"]), rows[0] if rows else None)
        st = {"rows": rows, "sel": front["id"] if front else None, "gone": [], "q": "", "only": False}
    return st


def selected(st):
    return next((r for r in st["rows"] if str(r["id"]) == str(st["sel"])), st["rows"][0] if st["rows"] else None)


def _pages():
    return [p for s in access.sections() for p in s["pages"]]


def take_post(st, post):
    """Merge the posted fields: the search, and the picked group's name, description and ticked permissions."""
    st["q"] = post.get("q", st["q"]).strip()[:40]
    e = selected(st)
    if not e or post.get("editing") != str(e["id"]):
        return
    if "desc" in post:
        e["desc"] = post.get("desc", "").strip()[:90]
    if e["owners"]:
        return
    if "name" in post:
        e["name"] = post.get("name", "").strip()[:30]
    if post.get("perms_shown") == "1":
        before, ticked = set(e["perms"]), set(post.getlist("perm")) & set(access.all_codes())
        for p in _pages():  # view untaken takes the rest with it (edit and delete come back with view in tidy)
            view = f"admin.{p['id']}.view"
            if view in before and view not in ticked:
                ticked -= set(access.page_codes(p["id"]))
        e["perms"] = access.tidy(ticked)


def _set(e, codes, on):
    have = set(e["perms"])
    e["perms"] = access.tidy(have | set(codes) if on else have - set(codes))


def apply_op(st, op) -> str:
    e = selected(st)
    name, _sep, arg = op.partition(":")
    if name == "pick":
        found = next((r for r in st["rows"] if str(r["id"]) == arg), None)
        if found:
            st["sel"] = found["id"]
    elif name == "only":
        st["only"] = not st["only"]
    elif name in ("new", "dup"):
        if name == "dup" and not e:
            return ""
        used = {r["color"] for r in st["rows"]}
        color = next((c for c in COLORS if c not in used), COLORS[len(st["rows"]) % len(COLORS)])
        names = {r["name"].lower() for r in st["rows"]}
        base = _("New group") if name == "new" else _("%(name)s copy") % {"name": e["name"]}
        label, n = base[:30], 2
        while label.lower() in names:
            label, n = f"{base[:27]} {n}", n + 1
        row = {"id": f"n{timezone.now().strftime('%H%M%S%f')}", "name": label, "desc": e["desc"] if name == "dup" else "",
               "color": color, "icon": "people", "owners": False,
               "perms": (access.all_codes() if e["owners"] else list(e["perms"])) if name == "dup" else []}
        st["rows"].append(row)
        st["sel"] = row["id"]
        if name == "dup":
            return _("Duplicated %(name)s. Rename it and adjust.") % {"name": e["name"]}
    elif not e or e["owners"]:
        return ""
    elif name == "delete":
        st["rows"] = [r for r in st["rows"] if r is not e]
        if isinstance(e["id"], int):
            st["gone"].append(e["id"])
        st["sel"] = next((r["id"] for r in st["rows"] if not r["owners"]), st["rows"][0]["id"] if st["rows"] else None)
        n = members(e["id"]) if isinstance(e["id"], int) else 0
        return (ngettext("%(name)s deleted. %(n)d person loses its permissions. Save to keep it.",
                         "%(name)s deleted. %(n)d people lose its permissions. Save to keep it.", n) if n
                else _("%(name)s deleted. Save to keep it.")) % {"name": e["name"] or _("Group"), "n": n}
    elif name == "row":
        codes = access.page_codes(arg)
        _set(e, codes, not set(codes) <= set(e["perms"]))
    elif name == "col" and arg in dict((k, 1) for k, *_rest in access.LEVELS):
        codes = [f"admin.{p['id']}.{arg}" for p in _pages() if arg in p["levels"]]
        _set(e, codes, not set(codes) <= set(e["perms"]))
    elif name == "sec":
        sec_id, _sep, level = arg.partition(":")
        sec = next((s for s in access.sections() if s["id"] == sec_id), None)
        if sec:
            every = [c for p in sec["pages"] for c in access.page_codes(p["id"])]
            _set(e, every, False)
            if level == "view":
                _set(e, [f"admin.{p['id']}.view" for p in sec["pages"]], True)
            elif level == "all":
                _set(e, every, True)
    return ""


def members(group_id) -> int:
    return User.objects.filter(groups__id=group_id, is_staff=True).count()


def problems(st) -> dict:
    out = {}
    names = [r["name"].strip().lower() for r in st["rows"]]
    for r in st["rows"]:
        if not r["name"].strip():
            out[str(r["id"])] = _("Give the group a name.")
        elif names.count(r["name"].strip().lower()) > 1:
            out[str(r["id"])] = _("Another group already has this name.")
    return out


def is_dirty(st) -> bool:
    return bool(st["gone"]) or st["rows"] != saved()


@transaction.atomic
def save(st):
    Group.objects.filter(pk__in=st["gone"], info__owners=False).delete()
    keep = [r for r in st["rows"] if isinstance(r["id"], int)]
    for r in keep:  # out of the way first, so two groups can swap names
        Group.objects.filter(pk=r["id"]).exclude(name=r["name"]).update(name=f"…{r['id']}")
    for position, r in enumerate(st["rows"]):
        if isinstance(r["id"], int):
            group = Group.objects.get(pk=r["id"])
            if group.name != r["name"]:
                group.name = r["name"]
                group.save(update_fields=["name"])
            info = GroupInfo.objects.get(group=group)
        else:
            group = Group.objects.create(name=r["name"])
            info = GroupInfo(group=group)
        info.description, info.color, info.icon, info.position = r["desc"], r["color"], r["icon"], position
        info.perms = [] if info.owners else access.tidy(r["perms"])
        info.save()


def page(st) -> dict:
    errs = problems(st)
    dirty = is_dirty(st)
    if errs:
        status, kind = _("Each group needs its own name"), "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""
    every = access.all_codes()
    total = len(every)
    people = {}
    for u in User.objects.filter(is_staff=True, groups__isnull=False).distinct().prefetch_related("groups").order_by("first_name", "last_name", "id"):
        for g in u.groups.all():
            people.setdefault(g.id, []).append(u)
    e = selected(st)
    rows = []
    for r in st["rows"]:
        n = len(people.get(r["id"], []))
        held = total if r["owners"] else len(r["perms"])
        rows.append({"r": r, "on": r is e, "icon": GROUP_ICONS.get(r["icon"], GROUP_ICONS["people"]), "name": r["name"] or _("Untitled group"),
                     "sub": " · ".join([ngettext("%(n)d person", "%(n)d people", n) % {"n": n},
                                        _("every permission") if r["owners"] else _("%(n)d of %(total)d") % {"n": held, "total": total}]),
                     "pct": round(held * 100 / total) if total else 0})
    d = None
    if e:
        lock = e["owners"]
        has = set(every) if lock else set(e["perms"])
        mine = people.get(e["id"], [])

        def toggles(items):
            return [{"code": code, "name": label, "desc": desc, "icon": path, "on": code in has} for code, label, desc, path in items]

        apps, actions = toggles(access.APPS), toggles(access.ACTIONS)
        q = st["q"].lower()
        levels = [k for k, *_rest in access.LEVELS]
        sections, shown_any = [], False
        for s in access.sections():
            codes = [c for p in s["pages"] for c in access.page_codes(p["id"])]
            views = [f"admin.{p['id']}.view" for p in s["pages"]]
            n = sum(c in has for c in codes)
            level = ("none" if n == 0 else "all" if n == len(codes) else
                     "view" if n == len(views) and all(v in has for v in views) else "some")
            pages = []
            for p in s["pages"]:
                pc = access.page_codes(p["id"])
                hidden = (st["only"] and not any(c in has for c in pc)) or bool(
                    q and q not in str(p["name"]).lower() and q not in p["id"] and q not in str(s["name"]).lower())
                cells = [{"level": lv, "code": f"admin.{p['id']}.{lv}", "has": lv in p["levels"], "on": f"admin.{p['id']}.{lv}" in has}
                         for lv in levels]
                pages.append({"id": p["id"], "name": p["name"], "hidden": hidden, "cells": cells, "full": all(c in has for c in pc)})
            visible = any(not p["hidden"] for p in pages)
            shown_any = shown_any or visible
            sections.append({"id": s["id"], "name": s["name"], "icon": s["icon"], "count": f"{n}/{len(codes)}", "lit": n > 0,
                             "pages": pages, "hidden": not visible,
                             "quick": [(k, label, level == k) for k, label in (("none", _("None")), ("view", _("View only")), ("all", _("Everything")))]})
        cols = []
        for k, label, title in access.LEVELS:
            codes = [f"admin.{p['id']}.{k}" for p in _pages() if k in p["levels"]]
            full = bool(codes) and all(c in has for c in codes)
            cols.append({"level": k, "label": label, "lit": any(c in has for c in codes),
                         "title": (_("Clear %(what)s on every page") if full else _("Give %(what)s on every page")) % {"what": str(label).lower()}})
        n = len(mine)
        d = {"e": e, "key": str(e["id"]), "lock": lock, "err": errs.get(str(e["id"]), ""),
             "faces": [{"ini": initials(u.first_name, u.last_name, u.username), "face": face(u.id),
                        "name": full_name(u.first_name, u.last_name, u.username)} for u in mine[:5]],
             "members": ", ".join(u.first_name or u.username for u in mine) if mine else _("Nobody yet. Add people from their staff profile."),
             "total": (_("All %(n)d permissions") % {"n": total}) if lock else (_("%(n)d of %(total)d permissions") % {"n": len(has), "total": total}),
             "can_delete": not lock,
             "special": [{"title": _("Apps"), "sub": _("Which Pavilion apps they can open."), "items": apps,
                          "count": f"{sum(i['on'] for i in apps)}/{len(apps)}"},
                         {"title": _("Actions"), "sub": _("Things that need trust, wherever they happen."), "items": actions,
                          "count": f"{sum(i['on'] for i in actions)}/{len(actions)}"}],
             "cols": cols, "sections": sections, "no_match": not shown_any,
             "admin_off": not lock and "app.admin" not in has and any(c.startswith("admin.") for c in has),
             "people_n": n}
    return {"st": st, "status": status, "status_kind": kind, "can_save": dirty and not errs, "rows": rows, "d": d,
            "foot": ngettext("%(n)d group", "%(n)d groups", len(st["rows"])) % {"n": len(st["rows"])}}
