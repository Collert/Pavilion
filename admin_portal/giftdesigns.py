"""
Gift card designs (design: AdminGiftDesigns): gift_cards.GiftCardDesign, how cards look and where they're sold. One
form with Save: the list in the order buyers see them (moved up and down), and the picked design's picture (kept as
soon as it's picked, data-upload), store name and ink, name and default message, amounts (plus buyers' own between a
lowest and highest), where it's sold (online store, register) and when (all year, or a season that repeats every
year), how it shows online and on the register, and its sales. Deleting one that sold cards archives it.

    request.session["admin_giftdesigns"] = {"rows": [{"id": 3 or "n…", "name", "image": media name or "", "color", "ink",
        "show_text", "amounts": [25, 50], "custom", "min", "max", "message", "online", "pos", "seasonal", "from", "to"}],
        "sel", "gone": [ids], "pv": "online"|"pos", "pick": {id: amount}, "new_amt"}
"""
import datetime
import re
import uuid

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, gettext_lazy as _l

from gift_cards.models import GiftCard, GiftCardDesign

KEY = "admin_giftdesigns"
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
WHERE = [("online", _l("Online store"), _l("Emailed to the recipient"),
          "M3 12h18 M12 3a14 14 0 0 1 0 18 M12 3a14 14 0 0 0 0 18 M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z"),
         ("pos", _l("Register"), _l("Printed or emailed at checkout"), "M4 12h16v8H4z M7 12V8h10v4 M9 4h6v4H9z")]


def row_of(d: GiftCardDesign) -> dict:
    year = timezone.localdate().year
    return {"id": d.id, "name": d.name, "image": d.image.name if d.image else "", "color": d.color, "ink": d.ink, "show_text": d.show_text,
            "amounts": sorted(int(a) for a in d.amounts), "custom": d.custom, "min": str(d.min_amount), "max": str(d.max_amount),
            "message": d.message, "online": d.online, "pos": d.on_register, "seasonal": d.seasonal,
            "from": (d.show_from or datetime.date(year, 11, 1)).isoformat(), "to": (d.show_until or datetime.date(year, 12, 31)).isoformat()}


def designs():
    return GiftCardDesign.objects.filter(archived=False)


def saved() -> list:
    return [row_of(d) for d in designs()]


def state(session) -> dict:
    st = session.get(KEY)
    if not st:
        rows = saved()
        st = {"rows": rows, "sel": rows[0]["id"] if rows else None, "gone": [], "pv": "online", "pick": {}, "new_amt": ""}
    return st


def selected(st):
    return next((r for r in st["rows"] if str(r["id"]) == str(st["sel"])), st["rows"][0] if st["rows"] else None)


def take_post(st, post):
    if "new_amt" in post:
        st["new_amt"] = re.sub(r"[^0-9]", "", post.get("new_amt", ""))[:4]
    e = selected(st)
    if not e or post.get("editing") != str(e["id"]):
        return
    for f, size in (("name", 30), ("message", 80)):
        if f in post:
            e[f] = post.get(f, "")[:size]
    for f in ("min", "max"):
        if f in post:
            e[f] = re.sub(r"[^0-9]", "", post.get(f, ""))[:4]
    for f in ("from", "to"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", post.get(f, "")):
            e[f] = post[f]


def take_image(st, upload) -> str:
    """Keep a picked picture straight away (its name goes in the draft). An error message, or ""."""
    e = selected(st)
    if not e:
        return ""
    if upload.content_type not in IMAGE_TYPES:
        return _("Use a JPG or PNG picture.")
    if upload.size > 5 * 1024 * 1024:
        return _("That picture is too big (5 MB at most).")
    e["image"] = default_storage.save(f"files/gift-cards/designs/{uuid.uuid4().hex[:12]}.{IMAGE_TYPES[upload.content_type]}", upload)
    return ""


def blank(name) -> dict:
    year = timezone.localdate().year
    return {"id": f"n{timezone.now().strftime('%H%M%S%f')}", "name": name, "image": "", "color": "#1F4A3A", "ink": "light", "show_text": True,
            "amounts": [25, 50], "custom": False, "min": "10", "max": "250", "message": "", "online": False, "pos": False,
            "seasonal": False, "from": f"{year}-11-01", "to": f"{year}-12-31"}


def apply_op(st, op) -> str:
    e = selected(st)
    name, _sep, arg = op.partition(":")
    rows = st["rows"]
    if name == "pick":
        found = next((r for r in rows if str(r["id"]) == arg), None)
        if found:
            st["sel"] = found["id"]
            st["new_amt"] = ""
    elif name in ("up", "down"):
        i = next((n for n, r in enumerate(rows) if str(r["id"]) == arg), None)
        j = None if i is None else i + (-1 if name == "up" else 1)
        if j is not None and 0 <= j < len(rows):
            rows[i], rows[j] = rows[j], rows[i]
    elif name == "pv" and arg in ("online", "pos"):
        st["pv"] = arg
    elif name == "new":
        row = blank(_("New design"))
        rows.append(row)
        st["sel"] = row["id"]
    elif not e:
        return ""
    elif name == "ink" and arg in ("light", "dark"):
        e["ink"] = arg
    elif name == "text":
        e["show_text"] = not e["show_text"]
    elif name == "amt" and arg.isdigit():
        st["pick"][str(e["id"])] = int(arg)
    elif name == "amt_rm" and arg.isdigit():
        e["amounts"] = [a for a in e["amounts"] if a != int(arg)]
    elif name == "amt_add":
        n = int(st["new_amt"]) if st["new_amt"].isdigit() else 0
        if 0 < n <= 1000 and n not in e["amounts"]:
            e["amounts"] = sorted(e["amounts"] + [n])
            st["pick"][str(e["id"])] = n
            st["new_amt"] = ""
    elif name == "flip" and arg in ("custom", "online", "pos"):
        e[arg] = not e[arg]
    elif name == "season" and arg in ("all", "seasonal"):
        e["seasonal"] = arg == "seasonal"
    elif name == "dup":
        copy = {**e, "id": f"n{timezone.now().strftime('%H%M%S%f')}", "name": (e["name"] + " " + _("copy"))[:30], "online": False, "pos": False,
                "amounts": list(e["amounts"])}
        rows.append(copy)
        st["sel"] = copy["id"]
        return _("Copied as “%(name)s”. It’s hidden until you turn on where it sells.") % {"name": copy["name"]}
    elif name == "delete" and len(rows) > 1:
        st["rows"] = [r for r in rows if r is not e]
        if isinstance(e["id"], int):
            st["gone"].append(e["id"])
        st["sel"] = st["rows"][0]["id"]
        return _("%(name)s deleted. Save to keep it.") % {"name": e["name"] or _("Design")}
    return ""


def _int(value):
    return int(value) if str(value).isdigit() else None


def errors(r, rows) -> dict:
    out = {}
    key = r["name"].strip().lower()
    if not key:
        out["name"] = _("Give the design a name.")
    elif any(x is not r and x["name"].strip().lower() == key for x in rows):
        out["name"] = _("Another design already has this name.")
    if not r["amounts"] and not r["custom"]:
        out["amt"] = _("Add at least one amount, or let buyers enter their own.")
    if r["custom"]:
        low, high = _int(r["min"]), _int(r["max"])
        if not low or not high or high <= low:
            out["range"] = _("The highest amount has to be more than the lowest.")
        elif high > 1000:
            out["range"] = _("Cards can hold up to $1,000.")
    if r["seasonal"] and r["to"] < r["from"]:
        out["when"] = _("The end date is before the start date.")
    return out


def problems(st) -> dict:
    return {str(r["id"]): errs for r in st["rows"] if (errs := errors(r, st["rows"]))}


def is_dirty(st) -> bool:
    return bool(st["gone"]) or st["rows"] != saved()


@transaction.atomic
def save(st):
    for d in GiftCardDesign.objects.filter(pk__in=st["gone"]):
        if d.cards.exists():
            d.archived = True  # cards already sold keep their look
            d.save(update_fields=["archived"])
        else:
            d.delete()
    for position, r in enumerate(st["rows"]):
        d = GiftCardDesign.objects.get(pk=r["id"]) if isinstance(r["id"], int) else GiftCardDesign()
        d.name, d.color, d.ink, d.show_text = r["name"].strip(), r["color"], r["ink"], r["show_text"]
        d.image = r["image"] or None
        d.amounts = sorted(r["amounts"])
        d.custom, d.message = r["custom"], r["message"].strip()
        if r["custom"]:
            d.min_amount, d.max_amount = int(r["min"]), int(r["max"])
        d.online, d.on_register, d.seasonal = r["online"], r["pos"], r["seasonal"]
        d.show_from = datetime.date.fromisoformat(r["from"]) if r["seasonal"] else None
        d.show_until = datetime.date.fromisoformat(r["to"]) if r["seasonal"] else None
        d.position = position
        d.save()


# ---------- the page ----------

def _short_date(iso) -> str:
    return date_format(datetime.date.fromisoformat(iso), "M j")


def as_design(r) -> GiftCardDesign:
    """An unsaved GiftCardDesign for a draft row (seasons and on-sale checks)."""
    return GiftCardDesign(name=r["name"], online=r["online"], on_register=r["pos"], seasonal=r["seasonal"],
                          show_from=datetime.date.fromisoformat(r["from"]), show_until=datetime.date.fromisoformat(r["to"]))


def status(r, today) -> tuple[str, str]:
    if not r["online"] and not r["pos"]:
        return _("Hidden"), "hidden"
    if not as_design(r).in_season(today):
        upcoming = today.strftime("%m-%d") < r["from"][5:] and (r["from"][5:] <= r["to"][5:])
        return (_("From %(date)s") % {"date": _short_date(r["from"])}, "soon") if upcoming else (_("Out of season"), "hidden")
    return _("On sale"), "on"


def picture(r) -> str:
    return default_storage.url(r["image"]) if r["image"] else ""


def page(st, store_name) -> dict:
    today = timezone.localdate()
    errs = problems(st)
    dirty = is_dirty(st)
    if errs:
        bad = next(r for r in st["rows"] if str(r["id"]) in errs)
        status_text, kind = _("Fix %(name)s to save") % {"name": bad["name"].strip() or _("the new design")}, "bad"
    else:
        status_text, kind = (_("Unsaved changes"), "dirty") if dirty else (_("All changes saved"), "")
    sales = {row["design"]: row for row in GiftCard.objects.exclude(source=GiftCard.STAFF).values("design")
             .annotate(n=Count("id"), value=Sum("original"))}
    e = selected(st)
    rows = []
    for i, r in enumerate(st["rows"]):
        label, key = status(r, today)
        rows.append({"r": r, "on": r is e, "st": label, "st_key": key, "pic": picture(r), "first": f"${r['amounts'][0]}" if r["amounts"] else "",
                     "amounts": (" · ".join(f"${a}" for a in r["amounts"]) if r["amounts"] else _("No set amounts")) + (" · " + _("custom") if r["custom"] else ""),
                     "top": i == 0, "bottom": i == len(st["rows"]) - 1})
    on_sale = sum(1 for x in rows if x["st_key"] == "on")
    sold = sum(row["n"] for row in sales.values())
    return {"st": st, "status": status_text, "status_kind": kind, "can_save": dirty and not errs, "rows": rows, "store": store_name,
            "foot_left": _("%(n)d on sale now") % {"n": on_sale}, "foot_right": _("%(n)d cards sold") % {"n": sold},
            "d": editor(st, e, errs.get(str(e["id"]), {}), sales, today) if e else None}


def editor(st, e, err, sales, today) -> dict:
    picked = st["pick"].get(str(e["id"]))
    if picked not in e["amounts"]:
        picked = e["amounts"][min(1, len(e["amounts"]) - 1)] if e["amounts"] else None
    amount_text = f"${picked}" if picked else (f"${e['min'] or 0}+" if e["custom"] else "")
    new = int(st["new_amt"]) if st["new_amt"].isdigit() else None
    new_err = (_("$%(n)s is already there.") % {"n": new} if new and new in e["amounts"]
               else _("Use whole dollars, up to $1,000.") if st["new_amt"] and (not new or new > 1000) else "")
    where = " " + _("and") + " "
    places = [p for p, on in ((_("online"), e["online"]), (_("at the register"), e["pos"])) if on]
    if not places:
        when = _("Hidden. Turn on the online store or register to sell it.")
    elif e["seasonal"]:
        when = (_("The end date is before the start date.") if e["to"] < e["from"]
                else _("Sold %(where)s from %(start)s to %(end)s each year.") % {"where": where.join(places), "start": _short_date(e["from"]), "end": _short_date(e["to"])})
    else:
        when = _("Sold %(where)s all year.") % {"where": where.join(places)}
    mine = sales.get(e["id"], {}) if isinstance(e["id"], int) else {}
    n, value = mine.get("n", 0), mine.get("value") or 0
    total = sum((row["value"] or 0) for row in sales.values())
    label, key = status(e, today)
    pv_mode = st.get("pv", "online")
    off = not (e["online"] if pv_mode == "online" else e["pos"])
    tiles = []
    if pv_mode == "pos":
        others = [r for r in st["rows"] if r["pos"] and r is not e and status(r, today)[1] == "on"][:2]
        for r in others + ([e] if e["pos"] or off else []):
            for a in r["amounts"][:2]:
                tiles.append({"r": r, "amount": f"${a}", "me": r is e, "pic": picture(r)})
    note = (_("Not sold %(where)s right now. This is how it would look.") % {"where": _("online") if pv_mode == "online" else _("at the register")} if off
            else _("%(status)s. It shows up automatically when its season starts.") % {"status": label} if key != "on"
            else _("Each amount is a button in the register’s Gift cards tab. This design is outlined.") if pv_mode == "pos"
            else _("Buyers pick an amount, add a message and who it’s for, and it’s emailed."))
    return {
        "e": e, "key": str(e["id"]), "err": err, "pic": picture(e), "amount_text": amount_text, "picked": picked,
        "image_hint": (e["image"].rsplit("/", 1)[-1] + " · " if e["image"] else "") + _("1600 × 1000 px works best. JPG or PNG, up to 5 MB."),
        "new_err": new_err, "add_off": not new or bool(new_err),
        "custom_hint": (_("An “Other amount” box between $%(low)s and $%(high)s.") % {"low": e["min"] or 0, "high": e["max"] or 0} if e["custom"]
                        else _("Off. Buyers pick one of the amounts above.")),
        "where": [(k, label_, desc, icon, e[k]) for k, label_, desc, icon in WHERE], "when": when,
        "stats": [(_("Cards sold"), str(n)), (_("Value sold"), f"${value / 1000:.1f}k" if value >= 1000 else f"${value:,.0f}"),
                  (_("Average card"), f"${value / n:,.0f}" if n else "—"), (_("Share of sales"), f"{round(value * 100 / total)}%" if total else "—")],
        "del_hint": (_("The %(n)d cards already sold keep working and keep this look.") % {"n": n} if n else _("No cards have been sold with it yet.")),
        "can_delete": len(st["rows"]) > 1,
        "pv": {"mode": pv_mode, "off": off or key != "on", "note": note, "tiles": tiles, "msg": e["message"] or _("Write a message"),
               "buy": _("Buy %(amount)s gift card") % {"amount": f"${picked}"} if picked else _("Buy gift card")},
    }
