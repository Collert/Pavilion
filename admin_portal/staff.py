"""
Staff (design: AdminStaff): the store's staff accounts (User.is_staff, with a users.Staff profile). One form with
Save: the list (search, role filter), the selected person's profile (name, email, phone, roles, groups, emergency
contact, can sign in, remove from staff). Edits are a draft in the session until Save; adding someone (promoting an
existing account or making a new one, optionally emailing an invite) and their photo take effect straight away.

Groups are django.contrib.auth Groups (users.GroupInfo: description, colour); members of the Owners group are made
superusers. The store always keeps one active owner, and nobody can turn off or remove themselves.

    request.session["admin_staff"] = {"rows": {user id: row}, "removed": [ids], "sel": id, "q", "role", "confirm": id,
                                      "add": None or {"tab": "find"|"new", "aq", "pick", "first", "last", "email", "phone", "invite"}}
    row = {"first", "last", "email", "phone", "roles": [ids], "groups": [ids], "em_name", "em_rel", "em_phone", "active"}
"""
import re

from django.contrib.auth.models import Group, User
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from users.models import GroupInfo, Role, Staff

from .team import face, full_name, initials, when

KEY = "admin_staff"
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
FIELDS = ("first", "last", "email", "phone", "em_name", "em_rel", "em_phone")


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def staff_users():
    return list(User.objects.filter(is_staff=True).select_related("staff_profile").prefetch_related("groups", "staff_profile__roles")
                .order_by("first_name", "last_name", "id"))


def profile(user) -> Staff:
    try:
        return user.staff_profile
    except Staff.DoesNotExist:
        return Staff.objects.create(user=user, since=timezone.localdate(user.date_joined))


def saved_row(user) -> dict:
    p = profile(user)
    return {"first": user.first_name, "last": user.last_name, "email": user.email, "phone": p.phone,
            "roles": sorted(r.id for r in p.roles.all()), "groups": sorted(g.id for g in user.groups.all()),
            "em_name": p.emergency_name, "em_rel": p.emergency_relation, "em_phone": p.emergency_phone, "active": user.is_active}


def owners_group():
    info = GroupInfo.objects.filter(owners=True).select_related("group").first()
    return info.group if info else None


def state(session) -> dict:
    st = session.get(KEY) or {}
    return {"rows": dict(st.get("rows") or {}), "removed": list(st.get("removed") or []), "sel": st.get("sel"),
            "q": st.get("q", ""), "role": st.get("role", "all"), "confirm": st.get("confirm"), "add": st.get("add")}


def store(session, st):
    session[KEY] = st


def rows(st, users) -> dict:
    """{user id: row} for everyone on staff in the draft (edited rows from the draft, the rest as saved)."""
    return {u.id: st["rows"].get(str(u.id)) or saved_row(u) for u in users if u.id not in st["removed"]}


def guarded(me, user) -> bool:
    """Only owners change an owner's profile (their email, say, which a password reset goes to)."""
    return not me.is_superuser and user.is_superuser


def take_post(st, post, users, me):
    """Merge the posted fields (the selected person's profile, the list's search, the add dialog) into the draft."""
    st["q"] = post.get("q", st["q"]).strip()[:60]
    sel = _int(post.get("sel"))
    by_id = {u.id: u for u in users}
    if sel in by_id and sel not in st["removed"] and post.get("profile") == "1" and not guarded(me, by_id[sel]):
        row = dict(st["rows"].get(str(sel)) or saved_row(by_id[sel]))
        for f in FIELDS:
            if f in post:
                row[f] = post.get(f, "").strip()[:80]
        valid_roles = set(Role.objects.filter(archived=False).values_list("id", flat=True))
        valid_groups = set(Group.objects.values_list("id", flat=True))
        before_roles = set(row["roles"])
        row["roles"] = sorted(i for i in (_int(x) for x in post.getlist("roles")) if i in valid_roles)
        groups = sorted(i for i in (_int(x) for x in post.getlist("groups")) if i in valid_groups)
        gained = set(row["roles"]) - before_roles
        if gained:  # a new role ticks its starting groups
            groups = sorted(set(groups) | set(Role.groups.through.objects.filter(role_id__in=gained).values_list("group_id", flat=True)))
        owners = owners_group()
        if owners and not me.is_superuser:  # only owners make (or unmake) owners
            groups = sorted((set(groups) - {owners.id}) | ({owners.id} & set(row["groups"])))
        current = rows(st, users)
        if owners and owners.id in row["groups"] and owners.id not in groups and sole_owner(current, sel, owners):
            groups = sorted(groups + [owners.id])  # a store always keeps an owner
            st["msg"] = _("Give someone else Owners first. A store always needs one owner.")
        row["groups"] = groups
        active = post.get("active") == "1"
        if not active and sel == me.id:
            active = True
            st["msg"] = _("You can’t turn off your own sign-in.")
        row["active"] = active
        if row == saved_row(by_id[sel]):
            st["rows"].pop(str(sel), None)
        else:
            st["rows"][str(sel)] = row
    if st.get("add") is not None:
        add = st["add"]
        add["aq"] = post.get("aq", add.get("aq", "")).strip()[:60]
        for f in ("first", "last", "email", "phone"):
            if f"new_{f}" in post:
                add[f] = post.get(f"new_{f}", "").strip()[:80]
        if "new_present" in post:
            add["invite"] = post.get("new_invite") == "1"


def sole_owner(current, uid, owners) -> bool:
    holders = [i for i, r in current.items() if owners.id in r["groups"] and r["active"]]
    return holders == [uid]


def problems(current, users, edited=None) -> dict:
    """{user id: what's wrong} for rows that can't be saved. With `edited` (ids), only those are checked: an account
    that was never edited here (an old one without an email, say) doesn't hold up everyone else."""
    out = {}
    emails = {}
    for uid, r in current.items():
        emails.setdefault(r["email"].lower(), []).append(uid)
    others = {e.lower(): uid for uid, e in User.objects.exclude(pk__in=list(current)).exclude(email="").values_list("id", "email")}
    names = {u.id: full_name(current[u.id]["first"], current[u.id]["last"]) for u in users if u.id in current}
    for uid, r in current.items():
        if edited is not None and uid not in edited:
            continue
        if not r["first"]:
            out[uid] = _("Add a first name.")
        elif not EMAIL.match(r["email"]):
            out[uid] = _("Enter a valid email. It’s what they sign in with.")
        elif len(emails[r["email"].lower()]) > 1:
            other = next(i for i in emails[r["email"].lower()] if i != uid)
            out[uid] = _("%(name)s already uses this email.") % {"name": names.get(other, "")}
        elif r["email"].lower() in others:
            out[uid] = _("Another account already uses this email.")
    return out


def edited(st) -> set:
    return {int(k) for k in st["rows"]}


def is_dirty(st) -> bool:
    return bool(st["rows"]) or bool(st["removed"])


@transaction.atomic
def save(st, users):
    owners = owners_group()
    by_id = {u.id: u for u in users}
    for key, row in st["rows"].items():
        user = by_id.get(int(key))
        if not user or user.id in st["removed"]:
            continue
        user.first_name, user.last_name, user.email, user.is_active = row["first"], row["last"], row["email"], row["active"]
        user.is_superuser = bool(owners and owners.id in row["groups"])
        user.save()
        user.groups.set(row["groups"])
        p = profile(user)
        p.phone, p.emergency_name, p.emergency_phone = row["phone"], row["em_name"], row["em_phone"]
        p.emergency_relation = row.get("em_rel", "")[:40]
        p.save()
        p.roles.set(row["roles"])
    for uid in st["removed"]:
        user = by_id.get(uid)
        if not user:
            continue
        user.is_staff = user.is_superuser = False
        user.save(update_fields=["is_staff", "is_superuser"])
        user.groups.clear()
        profile(user).roles.clear()


def apply_op(st, op, users, me) -> str:
    """List and profile buttons. Returns a toast ("" for none)."""
    name, _sep, arg = op.partition(":")
    current = rows(st, users)
    owners = owners_group()
    if name == "pick" and _int(arg) in current:
        st["sel"], st["confirm"] = _int(arg), None
    elif name == "role":
        st["role"] = arg if arg == "all" or _int(arg) else "all"
    elif name == "remove" and st["sel"] in current:
        uid = st["sel"]
        person = next((u for u in users if u.id == uid), None)
        if uid == me.id or (person and guarded(me, person)) or (owners and owners.id in current[uid]["groups"] and sole_owner(current, uid, owners)):
            return ""
        if st["confirm"] != uid:
            st["confirm"] = uid
            return ""
        st["removed"].append(uid)
        st["rows"].pop(str(uid), None)
        st["confirm"] = None
        row = current[uid]
        rest = [i for i in current if i != uid]
        st["sel"] = rest[0] if rest else None
        return _("%(name)s removed from staff. Save to keep it.") % {"name": full_name(row["first"], row["last"])}
    elif name == "keep":
        st["confirm"] = None
    return ""


# ---------- adding staff (straight away) ----------

def candidates(aq) -> list:
    """Accounts that aren't staff yet, with how many orders they've placed."""
    qs = User.objects.filter(is_staff=False).annotate(n_orders=Count("orders", distinct=True))
    q = aq.strip()
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q) | Q(username__icontains=q))
    return list(qs.order_by("first_name", "last_name", "id")[:40])


def promote(user) -> User:
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    profile(user)
    return user


def create(add) -> tuple[User | None, str]:
    """A new staff account from the add dialog: (user, problem)."""
    email = add.get("email", "").strip()
    if not add.get("first"):
        return None, _("Add a first name.")
    if not EMAIL.match(email):
        return None, _("That doesn’t look like an email.")
    if User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
        return None, _("An account with this email exists. Use Existing account instead.")
    user = User(username=email[:150], email=email, first_name=add["first"][:150], last_name=add.get("last", "")[:150], is_staff=True)
    user.set_unusable_password()
    user.save()
    p = profile(user)
    p.phone = add.get("phone", "")[:20]
    p.save(update_fields=["phone"])
    return user, ""


def invite(request, user):
    """Email a link where they set their own password (users.views.welcome)."""
    from django.contrib.auth.tokens import default_token_generator
    from django.urls import reverse
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from misc_tools.email import send
    from .models import StoreSettings
    link = request.build_absolute_uri(reverse("staff_welcome", args=[urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)]))
    from django.conf import settings
    send("staff_invite", user.email, {"name": user.first_name, "store": StoreSettings.load().name, "link": link}, user=user,
         fallback=settings.LANGUAGE_CODE)


# ---------- the page ----------

def _phone_digits(text):
    return re.sub(r"\D", "", text or "")


def page(st, users, me) -> dict:
    current = rows(st, users)
    by_id = {u.id: u for u in users}
    errs = problems(current, users, edited(st))
    dirty = is_dirty(st)
    if errs:
        bad = next(iter(errs))
        status, kind = _("Fix %(name)s’s profile to save") % {"name": full_name(current[bad]["first"], current[bad]["last"])}, "bad"
    elif dirty:
        status, kind = _("Unsaved changes"), "dirty"
    else:
        status, kind = _("All changes saved"), ""
    roles = list(Role.objects.filter(archived=False))
    role_by_id = {r.id: r for r in roles}
    groups = list(Group.objects.select_related("info").order_by("info__position", "id"))
    owners = owners_group()

    def has_em(r):
        return bool(r["em_name"] and r["em_phone"])

    used = [r for r in roles if any(r.id in row["roles"] for row in current.values())]
    filters = [("all", _("All"), len(current), st["role"] == "all")] + [
        (str(r.id), r.name, sum(r.id in row["roles"] for row in current.values()), st["role"] == str(r.id)) for r in used]
    q = st["q"].lower()
    qd = _phone_digits(q)
    shown = []
    for uid, r in current.items():
        if st["role"] != "all" and _int(st["role"]) not in r["roles"]:
            continue
        hay = f"{full_name(r['first'], r['last'])} {r['email']}".lower()
        if q and q not in hay and not (qd and qd in _phone_digits(r["phone"])):
            continue
        shown.append(uid)
    shown.sort(key=lambda uid: (not current[uid]["active"], current[uid]["first"].lower(), uid))
    sel = st["sel"] if st["sel"] in current else (shown[0] if shown else next(iter(current), None))
    st["sel"] = sel

    def photo(uid):
        p = getattr(by_id[uid], "staff_profile", None)
        return p.photo.url if p and p.photo else ""

    items = [{"id": uid, "on": uid == sel, "name": full_name(current[uid]["first"], current[uid]["last"]) + (" " + _("(you)") if uid == me.id else ""),
              "ini": initials(current[uid]["first"], current[uid]["last"]), "face": face(uid), "photo": photo(uid),
              "roles": " · ".join(role_by_id[i].name for i in current[uid]["roles"] if i in role_by_id) or _("No roles yet"),
              "off": not current[uid]["active"], "no_em": not has_em(current[uid])} for uid in shown]
    active_n = sum(r["active"] for r in current.values())
    off_n = len(current) - active_n
    no_em = sum(not has_em(r) for r in current.values())
    foot = ngettext("%(n)d active staff member", "%(n)d active staff members", active_n) % {"n": active_n}
    if off_n:
        foot += " · " + _("%(n)d off") % {"n": off_n}

    p = None
    if sel:
        r = current[sel]
        user = by_id[sel]
        prof = profile(user)
        seen = max([d for d in (user.last_login, user.register_sign_ins.aggregate(m=Max("last_used"))["m"]) if d], default=None)
        is_owner_sole = bool(owners and owners.id in r["groups"] and sole_owner(current, sel, owners))
        you = sel == me.id
        em_warn = (_("No emergency contact on file yet.") if not r["em_name"] and not r["em_phone"]
                   else _("Add both a name and a phone number.") if not has_em(r) else "")
        p = {"id": sel, "r": r, "you": you, "face": face(sel), "ini": initials(r["first"], r["last"]), "photo": photo(sel),
             "err": errs.get(sel, ""), "first_bad": not r["first"], "email_bad": sel in errs and bool(r["first"]),
             "roles": [{"r": x, "on": x.id in r["roles"]} for x in roles],
             "groups": [{"g": g, "desc": getattr(getattr(g, "info", None), "description", ""), "on": g.id in r["groups"],
                         "locked": bool(owners and g.id == owners.id and (is_owner_sole or not me.is_superuser))} for g in groups],
             "guarded": guarded(me, user),
             "group_warn": (_("No groups yet, so they can’t open any part of Pavilion.") if not r["groups"]
                            else _("Only owner. Someone else needs Owners before this can be removed.") if is_owner_sole else ""),
             "em_warn": em_warn,
             "active_hint": _("This is you.") if you else _("Off for seasonal staff or leave. Their profile stays.") if r["active"]
             else _("Off. They can’t sign in to the register or admin."),
             "since": date_format(prof.since, "M j, Y"), "seen": when(seen),
             "confirm": st["confirm"] == sel, "remove_off": you or is_owner_sole or guarded(me, user),
             "remove_text": _("You can’t remove yourself") if you else _("Only owner, can’t remove") if is_owner_sole
             else _("Yes, remove from staff") if st["confirm"] == sel else _("Remove from staff")}

    add = None
    if st.get("add") is not None:
        a = st["add"]
        found = candidates(a.get("aq", "")) if a["tab"] == "find" else []
        pick = next((u for u in found if u.id == a.get("pick")), None)
        email = a.get("email", "")
        taken = ""
        if email:
            match = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).first()
            if match:
                taken = _("Already on staff.") if match.is_staff else _("An account with this email exists. Use Existing account instead.")
            elif not EMAIL.match(email):
                taken = _("That doesn’t look like an email.")
        add = {**a, "accts": [{"u": u, "on": u.id == a.get("pick"), "ini": initials(u.first_name, u.last_name, u.username),
                                "name": full_name(u.first_name, u.last_name, u.username),
                                "meta": ngettext("Customer · %(n)d order", "Customer · %(n)d orders", u.n_orders) % {"n": u.n_orders}
                                if u.n_orders else _("Account · no orders")} for u in found],
               "email_err": taken,
               "off": (not pick) if a["tab"] == "find" else (not a.get("first") or not EMAIL.match(email) or bool(taken)),
               "go": (_("Make %(name)s staff") % {"name": pick.first_name or pick.username} if pick else _("Make staff")) if a["tab"] == "find"
               else (_("Create and send invite") if a.get("invite", True) else _("Create account"))}

    return {"st": st, "status": status, "status_kind": kind, "can_save": dirty and not errs, "filters": filters, "items": items,
            "foot": foot, "foot_warn": ngettext("%(n)d without an emergency contact", "%(n)d without an emergency contact", no_em) % {"n": no_em}
            if no_em else _("Everyone has an emergency contact"), "no_em": no_em, "p": p, "add": add, "none": not current}
