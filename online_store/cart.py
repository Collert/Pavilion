"""
The online store's cart, kept in the session (no browser storage): lines in pos_server.pricing's format plus a
`key` each, the promo code and gift cards, and the checkout's choices. Everything is priced on the server with
price_cart on every page; the browser never sends prices.

    request.session["cart"] = {"lines": [{"key": "a1b2c3", "type": "dish", "id": 5, "qty": 1, "cust": {...},
                                          "note": ""}], "coupon": "", "gifts": [<GiftCard id>]}

The dish sheet (`sheet()` / `line_from_post()`) turns a dish's customization kit (pricing.DishKit) into form
fields and back: g-<group key> (option ids), swap-<part> (a substitute component), out-<part> / extra-<part>
(leave a part off or ask for one more step), side-<extra> (a condiment on the side), note, qty. A combo asks for
each slot that has a choice (slot-<id> = the dish).
"""
import uuid
from decimal import Decimal

from django.utils.translation import gettext as _

from pos_server.models import Combo, OptionGroup
from pos_server.pricing import DishKit, OrderError, combo_queryset, dish_queryset, price_cart, to_cents

MAX_LINES = 40
MAX_QTY = 20
CENT = Decimal("0.01")


def money(value) -> str:
    return f"${Decimal(value or 0):,.2f}"


def load(request) -> dict:
    cart = request.session.get("cart")
    if not isinstance(cart, dict) or not isinstance(cart.get("lines"), list):
        cart = {"lines": [], "coupon": "", "gifts": []}
    cart.setdefault("coupon", "")
    cart.setdefault("gifts", [])
    return cart


def save(request, cart):
    request.session["cart"] = cart
    request.session.modified = True


def plain(lines):
    """The lines as price_cart takes them (without our keys)."""
    return [{k: v for k, v in line.items() if k != "key"} for line in lines]


def add(request, line):
    """Add a line, or more of an identical one."""
    cart = load(request)
    same = next((l for l in cart["lines"] if plain([l])[0] | {"qty": 0} == line | {"qty": 0}), None)
    if same:
        same["qty"] = min(MAX_QTY, same["qty"] + line["qty"])
    elif len(cart["lines"]) < MAX_LINES:
        cart["lines"].append({"key": uuid.uuid4().hex[:8], **line})
    save(request, cart)


def change(request, key, delta=0, remove=False):
    cart = load(request)
    for line in cart["lines"]:
        if line.get("key") == key:
            line["qty"] = max(0, min(MAX_QTY, int(line.get("qty", 1)) + delta))
            if remove or line["qty"] == 0:
                cart["lines"].remove(line)
            break
    save(request, cart)


def clear(request):
    save(request, {"lines": [], "coupon": "", "gifts": []})


def priced(request, channel="web", to_go=True, now=None):
    """(PricedCart or None, [problem messages]). Lines that can't be sold any more are taken out of the cart. A
    signed-in staff member's staff cards that work for this channel are on it (gift_cards/staff_cards.py)."""
    cart = load(request)
    problems = []
    good = []
    for line in cart["lines"]:
        try:
            price_cart(plain([line]), channel=channel, to_go=to_go, strict=False, now=now)
            good.append(line)
        except OrderError as error:
            problems.append(str(error))
    if len(good) != len(cart["lines"]):
        cart["lines"] = good
        save(request, cart)
    if not good:
        return None, problems
    coupon = cart.get("coupon", "")
    from gift_cards import staff_cards
    priced_cart = price_cart(plain(good), order_discount={"code": coupon} if coupon else None, channel=channel,
                             to_go=to_go, strict=False, now=now, staff_cards=staff_cards.cards_for(request.user, channel),
                             customer=request.user if request.user.is_authenticated else None)
    return priced_cart, problems


def rows(request, priced_cart, picture=None) -> list[dict]:
    """The cart's lines to show: key, title, what was changed, quantity, line total, and a dish to draw."""
    if priced_cart is None:
        return []
    out = []
    for line, raw in zip(priced_cart.lines, load(request)["lines"]):
        mods = [m for pick in line.picks for m in (pick.mods or [])] + list(line.mods or [])
        if line.kind == "combo":
            mods = [p.dish.title for p in line.picks] + mods
        dish = line.picks[0].dish if line.picks else None
        out.append({"key": raw["key"], "title": line.item.title, "mods": ", ".join(mods) or _("As is"),
                    "note": line.note, "qty": line.quantity, "total": line.gross, "dish": dish,
                    "picture": picture(line.item) if picture else None})
    return out


# ---------- the dish sheet ----------

def kit_of(dish_id):
    dish = dish_queryset().filter(pk=dish_id, archived=False).first()
    return DishKit(dish) if dish else None


def combo_of(combo_id):
    return combo_queryset().filter(pk=combo_id, active=True, archived=False).first()


def _ints(values):
    out = []
    for v in values:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            pass
    return out


def line_from_post(kit, post) -> dict:
    """A cart line for the dish from the sheet's form."""
    cust = {"parts": {}, "swaps": {}, "options": {}, "condiments": {}}
    for dc in kit.parts:
        swap = _ints([post.get(f"swap-{dc.id}")])
        if swap and swap[0] != dc.component_id and swap[0] in {c.component_id for c in dc.choices.all()}:
            cust["swaps"][str(dc.id)] = swap[0]
        if dc.adjustable:
            if post.get(f"out-{dc.id}"):
                cust["parts"][str(dc.id)] = 0
            elif post.get(f"extra-{dc.id}"):
                cust["parts"][str(dc.id)] = dc.standard + 1
    for key, group, _dc in kit.groups:
        ids = set(_ints(post.getlist(f"g-{key}")))
        valid = [o.id for o in group.options.all() if o.id in ids]
        if not valid:
            continue
        if group.pick == OptionGroup.ONE:
            cust["options"][key] = valid[0]
        else:
            cust["options"][key] = {str(i): 1 for i in valid}
    for extra in kit.condiments:
        if post.get(f"side-{extra.id}"):
            cust["condiments"][str(extra.id)] = 1
    cust = {k: v for k, v in cust.items() if v}
    note = post.get("note", "").strip()[:140]
    qty = max(1, min(MAX_QTY, _ints([post.get("qty")])[0] if _ints([post.get("qty")]) else 1))
    return {"type": "dish", "id": kit.dish.id, "qty": qty, "cust": cust, "note": note}


def combo_line_from_post(combo, post) -> dict:
    slots = {}
    for slot in combo.slots.all():
        options = list(slot.options.all())
        if slot.picks == 1 and len(options) > 1:
            chosen = _ints([post.get(f"slot-{slot.id}")])
            option = next((o for o in options if chosen and o.dish_id == chosen[0]), None)
            if option:
                slots[str(slot.id)] = [{"dish": option.dish_id}]
    qty = max(1, min(MAX_QTY, _ints([post.get("qty")])[0] if _ints([post.get("qty")]) else 1))
    return {"type": "combo", "id": combo.id, "qty": qty, "slots": slots, "note": post.get("note", "").strip()[:140]}


def sheet(kit, post=None) -> dict:
    """The dish sheet's groups, with what's picked (from `post`, else the defaults), the price and what's missing."""
    post = post or {}
    fresh = not post
    groups, missing = [], []
    get = (lambda k: post.get(k)) if not fresh else (lambda k: None)
    getlist = post.getlist if not fresh else (lambda k: [])
    for dc in kit.parts:
        choices = list(dc.choices.all())
        if choices:
            picked = _ints([get(f"swap-{dc.id}")]) or [dc.component_id]
            opts = [{"name": f"swap-{dc.id}", "value": dc.component_id, "label": dc.component.title, "price": "", "on": picked[0] == dc.component_id, "off": not dc.component.in_stock}]
            opts += [{"name": f"swap-{dc.id}", "value": ch.component_id, "label": ch.component.title,
                      "price": f"+{money(ch.price)}" if ch.price else "", "on": picked[0] == ch.component_id, "off": not ch.component.in_stock} for ch in choices]
            groups.append({"title": dc.label, "kind": "radio", "req": _("Required"), "required": True, "opts": opts})
    for key, group, _dc in kit.groups:
        chosen = set(_ints(getlist(f"g-{key}")))
        if fresh and group.pick == OptionGroup.ONE and group.default_option():
            chosen = {group.default_option().id}
        one = group.pick == OptionGroup.ONE
        opts = [{"name": f"g-{key}", "value": o.id, "label": o.title, "price": f"+{money(o.price)}" if o.price else "",
                 "on": o.id in chosen, "off": not o.available} for o in group.options.all()]
        if group.required and not any(o["on"] for o in opts):
            missing.append(group.title)
        groups.append({"title": group.title, "kind": "radio" if one else "checkbox",
                       "req": _("Required") if group.required else _("Optional · pick any") if not one else _("Optional"),
                       "required": group.required, "opts": opts, "note": group.note})
    leave = [dc for dc in kit.parts if dc.adjustable]
    if leave:
        groups.append({"title": _("Leave off"), "kind": "checkbox", "req": _("Optional"), "required": False,
                       "opts": [{"name": f"out-{dc.id}", "value": "1", "label": _("No %(part)s") % {"part": dc.label.lower()},
                                 "price": "", "on": bool(get(f"out-{dc.id}")), "off": False} for dc in leave]})
        extras = [dc for dc in leave if dc.max_extra]
        if extras:
            groups.append({"title": _("Extra"), "kind": "checkbox", "req": _("Optional"), "required": False,
                           "opts": [{"name": f"extra-{dc.id}", "value": "1", "label": _("Extra %(part)s") % {"part": dc.label.lower()},
                                     "price": f"+{money(dc.component.extra_price)}" if dc.component.extra_price else "",
                                     "on": bool(get(f"extra-{dc.id}")) and not get(f"out-{dc.id}"), "off": not dc.component.in_stock} for dc in extras]})
    if kit.condiments:
        from pos_server.pricing import condiment_of
        opts = []
        for extra in kit.condiments:
            condiment = condiment_of(extra)
            price = condiment.price if condiment else extra.price
            free = condiment.free_per_order if condiment else 0
            label = _("first one free") if condiment and free and not condiment.unlimited else _("free") if condiment and condiment.unlimited else ""
            opts.append({"name": f"side-{extra.id}", "value": "1", "label": extra.label, "on": bool(get(f"side-{extra.id}")), "off": not extra.available,
                         "price": label or (f"+{money(price)}" if price else "")})
        groups.append({"title": _("On the side"), "kind": "checkbox", "req": _("Optional · pick any"), "required": False, "opts": opts})
    qty = max(1, min(MAX_QTY, _ints([get("qty")])[0] if _ints([get("qty")]) else 1))
    line = line_from_post(kit, post) if not fresh else {"type": "dish", "id": kit.dish.id, "qty": qty, "cust": {}, "note": ""}
    if fresh:
        defaults = {key: g.default_option().id for key, g, _dc in kit.groups if g.pick == OptionGroup.ONE and g.default_option()}
        if defaults:
            line["cust"] = {"options": defaults}
    try:
        unit = price_cart([{**line, "qty": 1}], strict=False).lines[0].gross
        error = ""
    except OrderError as e:
        unit, error = kit.dish.price, str(e)
    return {"groups": groups, "missing": missing, "qty": qty, "unit": unit, "total": unit * qty, "error": error,
            "note": get("note") or ""}


def combo_sheet(combo, post=None) -> dict:
    post = post or {}
    groups = []
    for slot in combo.slots.all():
        options = list(slot.options.all())
        if slot.picks != 1 or len(options) < 2:
            continue
        chosen = _ints([post.get(f"slot-{slot.id}")]) if post else []
        # A required choice starts on its default, skipping sold-out dishes (ComboSlot.default_picks); an optional one
        # starts on "No thanks"
        defaults = slot.default_picks()
        picked = chosen[0] if chosen else (defaults[0].dish_id if defaults and slot.required else None)
        base = [p for p in combo.default_picks() if p.slot_id != slot.id]
        opts = []
        if not slot.required:
            opts.append({"name": f"slot-{slot.id}", "value": "", "label": _("No thanks"), "on": picked is None, "off": False, "price": ""})
        for o in options:
            extra = combo.unit_price(base + [o]) - combo.standard_price()
            opts.append({"name": f"slot-{slot.id}", "value": o.dish_id, "label": o.dish.title, "on": o.dish_id == picked,
                         "off": not o.dish.available, "price": f"+{money(extra)}" if extra > 0 else ""})
        groups.append({"title": slot.title, "kind": "radio", "req": _("Required") if slot.required else _("Optional"),
                       "required": slot.required, "opts": opts})
    qty = max(1, min(MAX_QTY, _ints([post.get("qty")])[0] if post and _ints([post.get("qty")]) else 1))
    line = combo_line_from_post(combo, post) if post else {"type": "combo", "id": combo.id, "qty": 1, "slots": {}}
    try:
        unit = price_cart([{**line, "qty": 1}], strict=False).lines[0].gross
        error = ""
    except OrderError as e:
        unit, error = combo.standard_price(), str(e)
    return {"groups": groups, "missing": [], "qty": qty, "unit": unit, "total": unit * qty, "error": error,
            "note": post.get("note", "") if post else ""}


def cents(value) -> int:
    return to_cents(value)
