"""
Roles (design: AdminRoles): the jobs people do (users.Role). One form with Save: the list (with who has each), and
the picked role's name, description, icon, colour, people (the same as ticking it on their Staff profile), starting
groups (ticked for whoever gets the role) and archiving (it comes off everyone's profile; their groups stay).

    request.session["admin_roles"] = {"rows": [{"id": 3 or "n1", "name", "desc", "icon", "color", "people": [user ids],
                                                "groups": [group ids]}], "sel": id, "gone": [ids]}
"""
from django.contrib.auth.models import Group, User
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from users.models import Role, Staff

from .team import PALETTE, ROLE_ICONS, face, full_name, initials

KEY = "admin_roles"


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def staff_users():
    return list(User.objects.filter(is_staff=True).order_by("first_name", "last_name", "id"))


def saved() -> list:
    rows = []
    for r in Role.objects.filter(archived=False).prefetch_related("staff", "groups"):
        rows.append({"id": r.id, "name": r.name, "desc": r.description, "icon": r.icon if r.icon in ROLE_ICONS else "knife", "color": r.color,
                     "people": sorted(s.user_id for s in r.staff.all() if s.user.is_staff), "groups": sorted(g.id for g in r.groups.all())})
    return rows


def state(session) -> dict:
    st = session.get(KEY)
    if not st:
        rows = saved()
        st = {"rows": rows, "sel": rows[0]["id"] if rows else None, "gone": []}
    return st


def selected(st):
    return next((r for r in st["rows"] if str(r["id"]) == str(st["sel"])), st["rows"][0] if st["rows"] else None)


def take_post(st, post):
    e = selected(st)
    if not e or post.get("editing") != str(e["id"]):
        return
    if "name" in post:
        e["name"] = post.get("name", "").strip()[:24]
    if "desc" in post:
        e["desc"] = post.get("desc", "").strip()[:120]
    valid = set(Group.objects.values_list("id", flat=True))
    e["groups"] = sorted(i for i in (_int(x) for x in post.getlist("groups")) if i in valid)
    add = _int(post.get("add_person"))
    if add and add not in e["people"] and User.objects.filter(pk=add, is_staff=True).exists():
        e["people"] = sorted(e["people"] + [add])
        person = User.objects.get(pk=add)
        st["msg"] = _("%(name)s is now a %(role)s.") % {"name": person.first_name or person.username, "role": (e["name"] or _("role")).lower()}


def apply_op(st, op) -> str:
    e = selected(st)
    name, _sep, arg = op.partition(":")
    if name == "pick":
        found = next((r for r in st["rows"] if str(r["id"]) == arg), None)
        if found:
            st["sel"] = found["id"]
    elif name == "new":
        used = {r["color"] for r in st["rows"]}
        color = next((c for c, _l in PALETTE if c not in used), PALETTE[0][0])
        new_id = f"n{timezone.now().strftime('%H%M%S%f')}"
        st["rows"].append({"id": new_id, "name": _("New role"), "desc": "", "icon": "knife", "color": color, "people": [], "groups": []})
        st["sel"] = new_id
    elif e and name == "icon" and arg in ROLE_ICONS:
        e["icon"] = arg
    elif e and name == "color" and arg in dict(PALETTE):
        e["color"] = arg
    elif e and name == "rm":
        e["people"] = [u for u in e["people"] if u != _int(arg)]
    elif e and name == "archive" and len(st["rows"]) > 1:
        st["rows"] = [r for r in st["rows"] if r is not e]
        if isinstance(e["id"], int):
            st["gone"].append(e["id"])
        st["sel"] = st["rows"][0]["id"]
        return _("%(name)s archived. Save to keep it.") % {"name": e["name"] or _("Role")}
    return ""


def problems(st) -> dict:
    out = {}
    names = [r["name"].lower() for r in st["rows"]]
    for r in st["rows"]:
        if not r["name"]:
            out[str(r["id"])] = _("Give the role a name.")
        elif names.count(r["name"].lower()) > 1:
            out[str(r["id"])] = _("Another role already has this name.")
    taken = set(Role.objects.filter(archived=True).values_list("name", flat=True))
    for r in st["rows"]:
        if str(r["id"]) not in out and not isinstance(r["id"], int) and r["name"] in taken:
            out[str(r["id"])] = _("An archived role has this name.")
    return out


def is_dirty(st) -> bool:
    return bool(st["gone"]) or st["rows"] != saved()


@transaction.atomic
def save(st):
    before = {r["id"]: set(r["people"]) for r in saved()}
    for position, r in enumerate(st["rows"]):
        fields = {"name": r["name"], "description": r["desc"], "icon": r["icon"], "color": r["color"], "position": position}
        if isinstance(r["id"], int):
            Role.objects.filter(pk=r["id"]).update(**fields)
            role = Role.objects.get(pk=r["id"])
        else:
            role = Role.objects.create(**fields)
        role.groups.set(r["groups"])
        had = before.get(r["id"], set())
        want = set(r["people"])
        for uid in want - had:
            person = User.objects.get(pk=uid)
            profile, _created = Staff.objects.get_or_create(user=person, defaults={"since": timezone.localdate(person.date_joined)})
            profile.roles.add(role)
            person.groups.add(*r["groups"])  # a new role brings its starting groups
        for profile in Staff.objects.filter(user_id__in=had - want):
            profile.roles.remove(role)
    for role in Role.objects.filter(pk__in=st["gone"]):
        role.archived = True
        role.save(update_fields=["archived"])
        for profile in role.staff.all():
            profile.roles.remove(role)


def page(st) -> dict:
    people = staff_users()
    by_uid = {u.id: u for u in people}
    errs = problems(st)
    dirty = is_dirty(st)
    if errs:
        status, kind = _("Each role needs its own name"), "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""
    e = selected(st)
    rows = []
    for r in st["rows"]:
        faces = [{"ini": initials(by_uid[u].first_name, by_uid[u].last_name, by_uid[u].username), "face": face(u)} for u in r["people"][:3] if u in by_uid]
        rows.append({"r": r, "on": r is e, "icon": ROLE_ICONS.get(r["icon"], ROLE_ICONS["knife"])[1], "faces": faces,
                     "more": max(0, len(r["people"]) - 3), "name": r["name"] or _("Untitled role"), "desc": r["desc"] or _("No description")})
    with_role = {u for r in st["rows"] for u in r["people"]}
    no_role = [u for u in people if u.is_active and u.id not in with_role]
    d = None
    if e:
        others = {u: [r["name"] for r in st["rows"] if r is not e and u in r["people"]] for u in e["people"]}
        n = len(e["people"])
        d = {"e": e, "key": str(e["id"]), "icon": ROLE_ICONS.get(e["icon"], ROLE_ICONS["knife"])[1], "err": errs.get(str(e["id"]), ""),
             "icons": [(k, label, path, k == e["icon"]) for k, (label, path) in ROLE_ICONS.items()],
             "colors": [(c, label, c == e["color"]) for c, label in PALETTE],
             "people_head": ngettext("%(n)d person with this role", "%(n)d people with this role", n) % {"n": n} if n else _("People with this role"),
             "people": [{"u": by_uid[u], "name": full_name(by_uid[u].first_name, by_uid[u].last_name, by_uid[u].username),
                         "ini": initials(by_uid[u].first_name, by_uid[u].last_name, by_uid[u].username), "face": face(u),
                         "also": ((_("Also %(roles)s") % {"roles": ", ".join(others[u])}) if others[u] else _("Only this role"))
                         + ("" if by_uid[u].is_active else " · " + _("Can’t sign in")), "off": not by_uid[u].is_active}
                        for u in e["people"] if u in by_uid],
             "add": [(u.id, full_name(u.first_name, u.last_name, u.username)) for u in people if u.id not in e["people"]],
             "groups": [{"g": g, "on": g.id in e["groups"]} for g in Group.objects.select_related("info").order_by("info__position", "id")],
             "archive_off": len(st["rows"]) <= 1,
             "archive_hint": (ngettext("It comes off %(n)d profile. Their groups and access don’t change.",
                                       "It comes off %(n)d profiles. Their groups and access don’t change.", n) % {"n": n})
             if n else _("Nobody has it, so nothing else changes.")}
    return {"st": st, "status": status, "status_kind": kind, "can_save": dirty and not errs, "rows": rows, "d": d,
            "foot": ngettext("%(n)d role", "%(n)d roles", len(st["rows"])) % {"n": len(st["rows"])},
            "foot_warn": (ngettext("%(n)d staff member without a role", "%(n)d staff without a role", len(no_role)) % {"n": len(no_role)})
            if no_role else _("Everyone has a role"), "no_role": bool(no_role)}
