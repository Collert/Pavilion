"""
Combos (design: AdminCombos, list + editor). Pricing is Combo.unit_price (set price + upcharges, or percent off the
picks); the editor works like the dish editor: one form, a draft posted back to show the sums, saved on Save.
"""
import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from pos_server.models import Combo, ComboSlot, ComboSlotOption, Dish, Menu, OrderItem, Station

TINTS = [("#DCEBE2", "#1F4A3A"), ("#FDEBD0", "#8A4B00"), ("#E3EEF6", "#2D5A84"), ("#FBE9F1", "#9B2C5E"), ("#F6F1E8", "#6B4E1F")]
CENT = Decimal("0.01")


def _dec(value):
    try:
        number = Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _money(value):
    return f"${Decimal(value).quantize(CENT):,}"


def initials(title):
    words = [w for w in title.split() if w[:1].isalpha() and w.lower() not in ("and", "a", "the")]
    return "".join(w[0].upper() for w in words[:2]) or title[:1].upper()


def status(active, menus):
    if not active:
        return {"key": "hidden", "label": _("Hidden")}
    if not menus:
        return {"key": "nomenu", "label": _("Not on a menu")}
    return {"key": "active", "label": _("Active")}


# ---------- prices from a draft (or a saved combo turned into one) ----------

def prices(draft, dishes):
    """
    {ala, combo, max, save}: the default picks bought separately, the combo with them, the combo with every
    priciest pick, and what the customer saves. `dishes` maps dish id (str) -> Dish.
    """
    percent = draft["pricing"] == Combo.PERCENT
    off = (_dec(draft["percent_off"]) or Decimal("0")) / 100
    ala = up_default = max_up = max_ala = Decimal("0")
    for slot in draft["slots"]:
        picks = max(_int(slot["picks"]) or 1, 1)
        options = [o for o in slot["options"] if o["dish"] in dishes]
        value = lambda o: dishes[o["dish"]].price  # noqa: E731
        upcharge = lambda o: _dec(o["price"]) or Decimal("0")  # noqa: E731
        if slot["required"]:
            ordered = sorted(options, key=lambda o: o["dish"] != slot["default"])
            for o in ordered[:picks]:
                ala += value(o)
                up_default += upcharge(o)
        for o in sorted(options, key=upcharge, reverse=True)[:picks]:
            max_up += upcharge(o)
            max_ala += value(o)
    if percent:
        combo, top = (ala * (1 - off)).quantize(CENT), (max_ala * (1 - off)).quantize(CENT)
    else:
        base = _dec(draft["price"]) or Decimal("0")
        combo, top = base + up_default, base + max_up
    return {"ala": ala, "combo": combo, "max": top, "save": ala - combo}


def new_combo():
    """An unsaved combo for the New combo editor (created on Save); its starting slots are in its draft."""
    return Combo(title="", ticket_name="", active=False)


def draft_from_combo(combo):
    if combo.pk:
        menus = sorted(m.id for m in combo.menu.all())
        slots = [{"id": str(s.id), "title": s.title, "picks": str(s.picks), "required": s.required,
                  "default": next((str(o.dish_id) for o in s.options.all() if o.is_default), ""),
                  "options": [{"id": str(o.id), "dish": str(o.dish_id), "price": str(o.price)} for o in s.options.all()]}
                 for s in combo.slots.all()]
    else:  # a new combo starts with three choices
        menus = []
        slots = [{"id": "", "title": str(title), "picks": "1", "required": True, "default": "", "options": []}
                 for title in (_("Main"), _("Side"), _("Drink"))]
    return {
        "title": combo.title, "ticket_name": combo.ticket_name, "description": combo.description,
        "pricing": combo.pricing, "price": str(combo.price), "percent_off": f"{combo.percent_off.normalize():f}",
        "active": combo.active, "menus": menus, "slots": slots,
    }


def draft_from_post(post):
    def count(name):
        return range(min(_int(post.get(name)) or 0, 100))

    slots = []
    for i in count("slot-count"):
        k = f"slot-{i}-"
        slots.append({"id": post.get(k + "id", ""), "title": post.get(k + "title", ""), "picks": post.get(k + "picks", "1"),
                      "required": bool(post.get(k + "required")), "default": post.get(k + "default", ""),
                      "options": [{"id": post.get(f"{k}opt-{j}-id", ""), "dish": post.get(f"{k}opt-{j}-dish", ""),
                                   "price": post.get(f"{k}opt-{j}-price", "0")} for j in count(k + "opt-count")]})
    return {
        "title": post.get("title", ""), "ticket_name": post.get("ticket_name", "").upper()[:12],
        "description": post.get("description", ""),
        "pricing": Combo.PERCENT if post.get("pricing") == Combo.PERCENT else Combo.FIXED,
        "price": post.get("price", ""), "percent_off": post.get("percent_off", ""),
        "active": bool(post.get("active")), "menus": sorted({m for m in (_int(v) for v in post.getlist("menus")) if m}),
        "slots": slots,
    }


def apply_op(draft, op, post):
    """Rows and steppers: add_slot, remove_slot:i, slot_up:i, slot_down:i, picks_less:i, picks_more:i, remove_option:i:j."""
    name, _sep, arg = (op or "").partition(":")
    args = [_int(a) for a in arg.split(":")] if arg else []
    slots = draft["slots"]
    i = args[0] if args and args[0] is not None and 0 <= args[0] < len(slots) else None
    if name == "add_slot":
        slots.append({"id": "", "title": "", "picks": "1", "required": True, "default": "", "options": []})
    elif i is not None:
        slot = slots[i]
        if name == "remove_slot":
            slots.pop(i)
        elif name in ("slot_up", "slot_down"):
            j = i - 1 if name == "slot_up" else i + 1
            if 0 <= j < len(slots):
                slots[i], slots[j] = slots[j], slots[i]
        elif name == "picks_less":
            slot["picks"] = str(max(1, (_int(slot["picks"]) or 1) - 1))
        elif name == "picks_more":
            slot["picks"] = str(min(max(1, len(slot["options"])), (_int(slot["picks"]) or 1) + 1))
        elif name == "remove_option" and len(args) == 2 and args[1] is not None and 0 <= args[1] < len(slot["options"]):
            removed = slot["options"].pop(args[1])
            if slot["default"] == removed["dish"]:
                slot["default"] = slot["options"][0]["dish"] if slot["options"] else ""
            slot["picks"] = str(min(_int(slot["picks"]) or 1, max(1, len(slot["options"]))))
    # A dish picked in a choice's "Add a dish" joins it whatever was pressed
    for slot_index, slot in enumerate(slots):
        dish = _int(post.get(f"slot-{slot_index}-add"))
        if dish and str(dish) not in {o["dish"] for o in slot["options"]} and Dish.objects.filter(pk=dish, archived=False).exists():
            slot["options"].append({"id": "", "dish": str(dish), "price": "0"})
            if slot["required"] and not slot["default"]:
                slot["default"] = str(dish)
    # A required choice always has a default
    for slot in slots:
        if slot["required"] and slot["options"] and slot["default"] not in {o["dish"] for o in slot["options"]}:
            slot["default"] = slot["options"][0]["dish"]
    return draft


def slot_errors(slot):
    out = []
    picks = _int(slot["picks"]) or 1
    if not slot["title"].strip():
        out.append(_("Give this choice a name."))
    if not slot["options"]:
        out.append(_("Add at least one dish."))
    elif picks > len(slot["options"]):
        out.append(ngettext("Asks for %(n)d picks but only has %(m)d dish.", "Asks for %(n)d picks but only has %(m)d dishes.",
                            len(slot["options"])) % {"n": picks, "m": len(slot["options"])})
    if slot["required"] and slot["options"] and not slot["default"]:
        out.append(_("Choose a default dish."))
    if any(_dec(o["price"]) is None or _dec(o["price"]) < 0 for o in slot["options"]):
        out.append(_("Check the upcharges."))
    return out


def problems(draft):
    count = sum(len(slot_errors(s)) for s in draft["slots"])
    count += 0 if draft["title"].strip() else 1
    count += 0 if draft["slots"] else 1
    if draft["pricing"] == Combo.FIXED:
        price = _dec(draft["price"])
        count += 0 if price is not None and price > 0 else 1
    else:
        pct = _dec(draft["percent_off"])
        count += 0 if pct is not None and 0 < pct < 100 else 1
    return count


def _canonical(draft):
    return (draft["title"].strip(), draft["ticket_name"].strip(), draft["description"].strip(), draft["pricing"],
            _dec(draft["price"]), _dec(draft["percent_off"]), draft["active"], tuple(draft["menus"]),
            tuple((s["id"], s["title"].strip(), _int(s["picks"]), s["required"], s["default"],
                   tuple((o["id"], o["dish"], _dec(o["price"])) for o in s["options"])) for s in draft["slots"]))


def is_dirty(draft, combo):
    return _canonical(draft) != _canonical(draft_from_combo(combo))


@transaction.atomic
def save_draft(draft, combo):
    """Write the draft to `combo` (a new Combo when it has no pk)."""
    combo.title = draft["title"].strip()[:140]
    combo.ticket_name = draft["ticket_name"].strip().upper()[:12]
    combo.description = draft["description"].strip()
    combo.pricing = draft["pricing"]
    if _dec(draft["price"]) is not None:
        combo.price = _dec(draft["price"]).quantize(CENT)
    if _dec(draft["percent_off"]) is not None:
        combo.percent_off = _dec(draft["percent_off"]).quantize(CENT)
    combo.active = draft["active"]
    combo.save()
    slots = {str(s.id): s for s in combo.slots.all()}
    keep = set()
    for position, d in enumerate(draft["slots"]):
        slot = slots.get(d["id"]) or ComboSlot(combo=combo)
        slot.title, slot.picks, slot.required, slot.position = d["title"].strip()[:60], max(_int(d["picks"]) or 1, 1), d["required"], position
        slot.save()
        keep.add(slot.id)
        options = {str(o.id): o for o in slot.options.all()}
        keep_options = set()
        for option_position, o in enumerate(d["options"]):
            option = options.get(o["id"]) or ComboSlotOption(slot=slot)
            option.dish_id, option.price, option.position = _int(o["dish"]), (_dec(o["price"]) or Decimal("0")).quantize(CENT), option_position
            option.is_default = slot.required and o["dish"] == d["default"]
            option.save()
            keep_options.add(option.id)
        slot.options.exclude(pk__in=keep_options).delete()
    combo.slots.exclude(pk__in=keep).delete()
    combo.menu.set(Menu.objects.filter(pk__in=draft["menus"]))
    return combo


# ---------- 30-day sales ----------

def sales(since=None):
    """{combo id: {sold, upgraded (share of combos with a paid upgrade), upcharges}} for the last 30 days."""
    since = since or timezone.now() - datetime.timedelta(days=30)
    out = {}
    items = (OrderItem.objects.filter(parent__isnull=True, combo__isnull=False, order__timestamp__gte=since, order__voided_at__isnull=True)
             .prefetch_related("children"))
    for item in items:
        row = out.setdefault(item.combo_id, {"sold": 0, "upgraded": 0, "upcharges": Decimal("0")})
        row["sold"] += item.quantity
        upcharges = sum((c.upcharge * c.quantity for c in item.children.all()), Decimal("0"))
        row["upcharges"] += upcharges
        if upcharges > 0:
            row["upgraded"] += item.quantity
    return out


# ---------- list ----------

def combo_list(query="", show="all"):
    combos = list(Combo.objects.filter(archived=show == "archived").prefetch_related("menu", "slots__options__dish").order_by("title"))
    q = (query or "").strip().lower()
    if q:
        combos = [c for c in combos if q in c.title.lower() or any(q in o.dish.title.lower() for s in c.slots.all() for o in s.options.all())]
    stats = sales()
    rows = []
    for index, combo in enumerate(combos):
        draft = draft_from_combo(combo)
        dishes = {str(o.dish_id): o.dish for s in combo.slots.all() for o in s.options.all()}
        p = prices(draft, dishes)
        menus = sorted(combo.menu.all(), key=lambda m: m.position)
        st = stats.get(combo.id, {"sold": 0, "upgraded": 0, "upcharges": Decimal("0")})
        if combo.pricing == Combo.PERCENT:
            price_sub = _("%(pct)s%% off the items") % {"pct": f"{combo.percent_off.normalize():f}"}
        else:
            price_sub = _("up to %(price)s") % {"price": _money(p["max"])} if p["max"] > p["combo"] else _("one price")
        rows.append({"combo": combo, "initials": initials(combo.title), "tint": TINTS[combo.id % len(TINTS)], "p": p,
                     "menus": " · ".join(m.title for m in menus) or _("Not on any menu"), "status": status(combo.active, menus),
                     "slots": [{"label": ("" if s.required else "+ ") + s.title + (f" ×{s.picks}" if s.picks > 1 else ""), "required": s.required}
                               for s in combo.slots.all()],
                     "price_sub": price_sub, "stats": st,
                     "sold_sub": (_("%(pct)d%% upgraded") % {"pct": round(st["upgraded"] / st["sold"] * 100)}) if st["sold"] else _("not sold yet")})
    filters = [("all", _("All"), lambda r: True), ("active", _("Active"), lambda r: r["combo"].active),
               ("hidden", _("Hidden"), lambda r: not r["combo"].active)]
    if show == "archived":
        chips = [{"key": "all", "label": _("All"), "count": Combo.objects.filter(archived=False).count()}]
        shown = rows
    else:
        show = show if show in {f[0] for f in filters} else "all"
        chips = [{"key": key, "label": label, "count": sum(1 for r in rows if test(r))} for key, label, test in filters]
        shown = [r for r in rows if dict((f[0], f[2]) for f in filters)[show](r)]
    archived = Combo.objects.filter(archived=True).count()
    if archived:
        chips.append({"key": "archived", "label": _("Archived"), "count": archived})

    live = [r for r in rows if r["combo"].active] if show != "archived" else []
    sold = sum(r["stats"]["sold"] for r in live)
    top = max(live, key=lambda r: r["stats"]["sold"], default=None)
    kpis = [
        {"label": _("Combos sold"), "value": f"{sold:,}", "sub": _("Last 30 days")},
        {"label": _("Average customer saving"), "value": _money(sum((r["p"]["save"] for r in live), Decimal("0")) / len(live)) if live else "$0.00",
         "sub": _("Against the default picks bought separately")},
        {"label": _("Upcharge revenue"), "value": _money(sum((r["stats"]["upcharges"] for r in live), Decimal("0"))),
         "sub": _("From premium picks, last 30 days")},
        {"label": _("Best seller"), "value": top["combo"].title if top and top["stats"]["sold"] else "—",
         "sub": _("%(pct)d%% of combos sold") % {"pct": round(top["stats"]["sold"] / sold * 100)} if top and sold else ""},
    ]
    return {"rows": shown, "filters": chips, "show": show, "query": query.strip(), "kpis": kpis}


# ---------- editor ----------

def editor(combo, draft, leave_ask=False):
    dish_ids = {o["dish"] for s in draft["slots"] for o in s["options"]}
    dishes = {str(d.id): d for d in Dish.objects.filter(pk__in=[i for i in (_int(x) for x in dish_ids) if i]).select_related("station")}
    p = prices(draft, dishes)
    percent = draft["pricing"] == Combo.PERCENT
    off = _dec(draft["percent_off"]) or Decimal("0")
    errors = problems(draft)
    dirty = is_dirty(draft, combo)
    stations = list(Station.objects.filter(archived=False))
    choices = [d for d in Dish.objects.filter(archived=False).select_related("station").order_by("station__position", "title")]

    slots = []
    for i, s in enumerate(draft["slots"]):
        picks = _int(s["picks"]) or 1
        used = {o["dish"] for o in s["options"]}
        options = []
        for j, o in enumerate(s["options"]):
            dish = dishes.get(o["dish"])
            if dish is None:
                continue
            options.append({"j": j, "o": o, "dish": dish, "default": s["required"] and o["dish"] == s["default"],
                            "in_combo": (dish.price * (100 - off) / 100).quantize(CENT)})
        if s["required"]:
            each = _("one") if picks == 1 else str(picks)
            hint = _("Customer must pick %(n)s. ") % {"n": each} + (
                _("Each pick is %(pct)s%% off its own price.") % {"pct": f"{off.normalize():f}"} if percent
                else _("Included in the combo price; upcharges are added on top."))
        else:
            each = _("one") if picks == 1 else _("up to %(n)d") % {"n": picks}
            hint = _("Customer can add %(n)s%(how)s, or skip it.") % {
                "n": each, "how": _(" at %(pct)s%% off") % {"pct": f"{off.normalize():f}"} if percent else _(" for the upcharge")}
        slots.append({"i": i, "s": s, "n": i + 1, "options": options, "hint": hint, "errors": slot_errors(s),
                      "less_off": picks <= 1, "more_off": picks >= max(1, len(s["options"])), "first": i == 0, "last": i == len(draft["slots"]) - 1,
                      "add": [{"group": st.name, "dishes": [d for d in choices if d.station_id == st.id and str(d.id) not in used]} for st in stations]})

    ala, save = p["ala"], p["save"]
    pct_save = round(save / ala * 100) if ala > 0 else 0
    calc = [{"label": _("Default picks bought separately"), "value": _money(ala), "kind": ""},
            {"label": _("Combo price"), "value": _money(p["combo"]), "kind": "big"},
            {"label": (_("Customer saves (%(pct)d%%)") % {"pct": pct_save}) if save > 0 else _("Customer saves"),
             "value": _money(save) if save > 0 else _("nothing"), "kind": "save" if save > 0 else "nosave"}]
    if p["max"] > p["combo"]:
        calc.append({"label": _("Combo with every premium pick"), "value": _money(p["max"]), "kind": ""})
    combo_share = max(0, min(100, float(p["combo"] / ala * 100))) if ala > 0 else 100
    warnings = []
    if not percent and (_dec(draft["price"]) or 0) > 0 and save <= 0 and ala > 0:
        warnings.append({"bad": True, "text": _("This costs the same or more than buying the default picks separately. Customers have no reason to choose it.")})
    elif ala > 0 and pct_save > 40:
        warnings.append({"bad": False, "text": _("That's a %(pct)d%% discount. Check it still covers food cost.") % {"pct": pct_save}})
    outs = [dishes[o["dish"]].title for s in draft["slots"] for o in s["options"] if o["dish"] in dishes and not dishes[o["dish"]].available]
    if outs:
        warnings.append({"bad": False, "text": ngettext("%(list)s is sold out today. The register greys it out inside the combo.",
                                                        "%(list)s are sold out today. The register greys them out inside the combo.", len(outs))
                         % {"list": ", ".join(outs)}})

    preview = []
    for s in draft["slots"]:
        picks = _int(s["picks"]) or 1
        default = dishes.get(s["default"])
        others = len(s["options"]) - (1 if default else 0)
        top_up = max((_dec(o["price"]) or Decimal("0") for o in s["options"]), default=Decimal("0"))
        if s["required"]:
            pick = (default.title + (_(" + %(n)d more") % {"n": picks - 1} if picks > 1 else "")) if default else _("Not set")
            more = (ngettext("or %(n)d other", "or %(n)d others", others) % {"n": others}
                    + (_(", up to +%(price)s") % {"price": _money(top_up)} if top_up and not percent else "")) if others > 0 else _("fixed")
        else:
            pick = _("Optional add-on")
            low = min((_dec(o["price"]) or Decimal("0") for o in s["options"]), default=Decimal("0"))
            more = (ngettext("%(n)d to choose", "%(n)d to choose", len(s["options"])) % {"n": len(s["options"])}
                    + (_(" · %(pct)s%% off") % {"pct": f"{off.normalize():f}"} if percent else _(" · from +%(price)s") % {"price": _money(low)})) if s["options"] else ""
        preview.append({"slot": s["title"] or _("Choice"), "pick": pick, "more": more})
    used_stations = []
    for s in draft["slots"]:
        for o in s["options"]:
            dish = dishes.get(o["dish"])
            if dish and dish.station.name not in used_stations:
                used_stations.append(dish.station.name)
    kitchen = _("Prints as “%(name)s”. Each pick fires to its own station") % {"name": draft["ticket_name"].strip() or "—"} + (
        ": " + ", ".join(used_stations) + "." if used_stations else ".")
    if not draft["active"]:
        menus_line = _("Hidden combos don't show on any menu or at the register.")
    elif draft["menus"]:
        menus_line = ngettext("Shows at the register and on menu boards whenever this menu is live.",
                              "Shows at the register and on menu boards whenever one of these menus is live.", len(draft["menus"]))
    else:
        menus_line = _("Active, but not on any menu yet, so nobody can order it.")
    return {
        "combo": combo, "d": draft, "title": draft["title"].strip() or _("Untitled combo"),
        "state": status(draft["active"], draft["menus"]),
        "status": (ngettext("%(n)d thing to fix", "%(n)d things to fix", errors) % {"n": errors}) if errors
        else _("Unsaved changes") if dirty else _("All changes saved"),
        "status_kind": "bad" if errors else "dirty" if dirty else "", "can_save": dirty and not errors, "leave_ask": leave_ask,
        "slots": slots, "percent": percent, "p": p, "calc": calc, "combo_share": round(combo_share, 1),
        "warnings": warnings, "preview": preview, "kitchen": kitchen,
        "from_price": (_("from %(price)s") % {"price": _money(p["combo"])}) if p["max"] > p["combo"] else _money(p["combo"]),
        "save_text": _("Save %(price)s") % {"price": _money(save)} if save > 0 else _("No saving"), "saves": save > 0,
        "menus": [{"menu": m, "on": m.id in draft["menus"]} for m in Menu.objects.all()], "menus_line": menus_line,
    }
