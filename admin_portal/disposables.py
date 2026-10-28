"""
Disposables (design: AdminDisposables, list + editor). A Disposable sits on the ingredient that holds its buying
(supplier, units per case, case price, so the cost) and its stock; it adds what it's made of, which bin it goes in,
and how orders use it. Orders count them off stock and charge their fees (pos_server/pricing.py, orders.py); the
numbers here come from DisposableUse and OrderFee rows.
"""
import datetime
import math
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from pos_server.models import Dish, Disposable, DisposableUse, Ingredient, Order, OrderFee, StorageLocation, Supplier

from . import ingredients as ingredient_page

CENT = Decimal("0.01")
MAX_COUNT = 10
BIN_LOOK = {"compost": "#12875A", "recycle": "#4E79B5", "landfill": "#7C8A83"}
MODE_HINTS = {"dish": _("Containers, cups, wraps"), "order": _("Bags, napkins, seals"), "guest": _("Catering plates, sets"),
              "request": _("Cutlery, straws, carriers")}


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


def _num(value):
    value = _dec(value) or Decimal("0")
    return f"{value.normalize():f}" if value != value.to_integral_value() else str(int(value))


def money(value):
    return f"${Decimal(value or 0).quantize(CENT):,}"


def money3(value):
    """$0.125 when there are fractions of a cent, else $0.12."""
    value = Decimal(value or 0)
    if 0 < value < 1 and value != value.quantize(CENT):
        return f"${value:.3f}"
    return money(value)


def bin_for(material, current):
    """The bin a material usually goes in (the design's rule): plastics and metal to recycling, mixed to garbage."""
    if material in ("pet", "pp", "alu"):
        return "recycle"
    if material == "mixed":
        return "landfill"
    return "compost" if current == "recycle" and material != "kraft" else current


def usage(since=None):
    """{disposable id: {"used", "spent", "charged"}} over the last 30 days."""
    since = since or timezone.now() - datetime.timedelta(days=30)
    out = {}
    for use in DisposableUse.objects.filter(order__timestamp__gte=since, order__voided_at__isnull=True):
        row = out.setdefault(use.disposable_id, {"used": 0, "spent": Decimal("0"), "charged": Decimal("0")})
        row["used"] += use.quantity
        row["spent"] += use.cost * use.quantity
    for fee in OrderFee.objects.filter(order__timestamp__gte=since, order__voided_at__isnull=True, disposable__isnull=False):
        out.setdefault(fee.disposable_id, {"used": 0, "spent": Decimal("0"), "charged": Decimal("0")})["charged"] += fee.amount
    return out


def rule_text(disposable, dishes):
    if disposable.counted == Disposable.PER_DISH:
        if not dishes:
            return _("No dishes picked")
        more = len(dishes) - 1
        return _("1 per %(dish)s") % {"dish": dishes[0].title.lower()} + (_(" +%(n)d more") % {"n": more} if more else "")
    if disposable.counted == Disposable.PER_ORDER:
        return _("%(n)d per order") % {"n": disposable.count}
    if disposable.counted == Disposable.PER_GUEST:
        return _("%(n)d per guest") % {"n": disposable.count}
    return _("On request")


# ---------- drafts ----------

def new_disposable():
    """An unsaved disposable for the New item editor (Save creates it and its ingredient)."""
    return Disposable(ingredient=Ingredient(title="", unit_of_measurement="ea", category="other", pack_size=100),
                      order_types=["pickup", "delivery"])


def draft_from_disposable(disposable):
    ingredient = disposable.ingredient
    return {
        "title": ingredient.title, "size": disposable.size, "category": disposable.category, "material": disposable.material,
        "material_was": disposable.material, "bin": disposable.bin, "order_types": list(disposable.order_types or []),
        "counted": disposable.counted, "dishes": sorted(str(d) for d in disposable.dishes.values_list("id", flat=True)) if disposable.pk else [],
        "count": str(disposable.count), "fee": disposable.fee, "fee_label": disposable.fee_label, "fee_price": f"{disposable.fee_price:.2f}",
        "supplier": ingredient.supplier.name if ingredient.supplier_id else "", "case_size": _num(ingredient.pack_size),
        "case_price": f"{ingredient.pack_price:.2f}" if ingredient.pack_price is not None else "",
        "inventory": _num(ingredient.inventory or 0), "inventory_was": _num(ingredient.inventory or 0), "par": _num(ingredient.par or 0),
        "storage": ingredient.storage.name if ingredient.storage_id else "",
    }


def draft_from_post(post):
    types = dict(Disposable.ORDER_TYPES)
    material = post.get("material") if post.get("material") in dict(Disposable.MATERIALS) else "paper"
    draft = {
        "title": post.get("title", ""), "size": post.get("size", "").strip()[:40],
        "category": post.get("category") if post.get("category") in dict(Disposable.CATEGORIES) else "containers",
        "material": material, "material_was": material, "bin": post.get("bin") if post.get("bin") in dict(Disposable.BINS) else "compost",
        "order_types": [t for t in types if t in post.getlist("order_types")],
        "counted": post.get("counted") if post.get("counted") in dict(Disposable.COUNTED) else Disposable.PER_DISH,
        "dishes": sorted({v for v in post.getlist("dishes") if _int(v)}),
        "count": str(min(max(_int(post.get("count")) or 1, 1), MAX_COUNT)),
        "fee": bool(post.get("fee")), "fee_label": post.get("fee_label", "").strip()[:60], "fee_price": post.get("fee_price", "0"),
        "supplier": post.get("supplier", "").strip()[:80], "case_size": post.get("case_size", ""), "case_price": post.get("case_price", ""),
        "inventory": post.get("inventory", "0"), "inventory_was": post.get("inventory_was", "0"), "par": post.get("par", "0"),
        "storage": post.get("storage", "").strip()[:60],
    }
    if post.get("material_was") and post.get("material_was") != material:  # a new material suggests its bin
        draft["bin"] = bin_for(material, draft["bin"])
    if draft["fee"] and post.get("fee_was") == "0":  # turning the fee on fills in a label and a price
        draft["fee_label"] = draft["fee_label"] or draft["title"].strip()[:60]
        if not (_dec(draft["fee_price"]) or 0) > 0:
            draft["fee_price"] = "0.25"
    return draft


def apply_op(draft, op):
    count = int(draft["count"])
    if op == "count_less":
        count = max(1, count - 1)
    elif op == "count_more":
        count = min(MAX_COUNT, count + 1)
    draft["count"] = str(count)
    return draft


def cost_each(draft):
    size, price = _dec(draft["case_size"]), _dec(draft["case_price"])
    return price / size if size and size > 0 and price is not None else None


def full_title(draft):
    return draft["title"].strip() + (f", {draft['size'].strip()}" if draft["size"].strip() else "")


def problems(draft, disposable):
    title = draft["title"].strip()
    if not title:
        return _("Give it a name")
    clash = Disposable.objects.filter(ingredient__title__iexact=title, size__iexact=draft["size"].strip(), ingredient__archived=False)
    if disposable.pk:
        clash = clash.exclude(pk=disposable.pk)
    if clash.exists():
        return _("An item with this name and size exists")
    if not (_dec(draft["case_size"]) or 0) > 0:
        return _("Set units per case")
    if draft["fee"] and not draft["fee_label"].strip():
        return _("Name the receipt line")
    numbers = [draft["inventory"], draft["par"], draft["fee_price"]] + ([draft["case_price"]] if draft["case_price"] else [])
    if any(_dec(v) is None or _dec(v) < 0 for v in numbers):
        return _("Check the highlighted numbers")
    return None


def _canonical(draft):
    return (draft["title"].strip(), draft["size"].strip(), draft["category"], draft["material"], draft["bin"], tuple(draft["order_types"]),
            draft["counted"], tuple(draft["dishes"]) if draft["counted"] == Disposable.PER_DISH else (),
            int(draft["count"]), draft["fee"], draft["fee_label"].strip() if draft["fee"] else "", _dec(draft["fee_price"]) if draft["fee"] else None,
            draft["supplier"].strip().lower(), _dec(draft["case_size"]), _dec(draft["case_price"]) if draft["case_price"] else None,
            _dec(draft["inventory"]), _dec(draft["par"]), draft["storage"].lower())


def is_dirty(draft, disposable):
    return _canonical(draft) != _canonical(draft_from_disposable(disposable))


@transaction.atomic
def save_draft(draft, disposable):
    ingredient = disposable.ingredient if disposable.pk else Ingredient(unit_of_measurement="ea", category="other")
    old_cost = ingredient.cost if ingredient.pk else None
    ingredient.title = draft["title"].strip()[:140]
    name = draft["supplier"].strip()
    ingredient.supplier = (Supplier.objects.filter(name__iexact=name).first() or Supplier.objects.create(name=name)) if name else None
    ingredient.pack_size = float(_dec(draft["case_size"]) or 1)
    ingredient.pack_price = _dec(draft["case_price"]).quantize(CENT) if draft["case_price"] and _dec(draft["case_price"]) is not None else None
    if _dec(draft["inventory"]) != _dec(draft["inventory_was"]) or not ingredient.pk:  # leave counts made elsewhere alone
        ingredient.inventory = float(_dec(draft["inventory"]) or 0)
    ingredient.par = float(_dec(draft["par"]) or 0) or None
    ingredient.storage = StorageLocation.named(draft["storage"])
    new_cost = (ingredient.pack_price / Decimal(str(ingredient.pack_size))).quantize(Decimal("0.000001")) if ingredient.pack_price is not None else None
    if old_cost is not None and new_cost is not None and new_cost != old_cost:
        ingredient.previous_cost = old_cost
    ingredient.save()
    disposable.ingredient = ingredient
    disposable.size, disposable.category, disposable.material, disposable.bin = draft["size"].strip(), draft["category"], draft["material"], draft["bin"]
    disposable.order_types, disposable.counted, disposable.count = list(draft["order_types"]), draft["counted"], int(draft["count"])
    disposable.fee = draft["fee"]
    disposable.fee_label = draft["fee_label"].strip() if draft["fee"] else ""
    disposable.fee_price = (_dec(draft["fee_price"]) or Decimal("0")).quantize(CENT) if draft["fee"] else Decimal("0")
    disposable.save()
    disposable.dishes.set(Dish.objects.filter(pk__in=[int(d) for d in draft["dishes"]]) if draft["counted"] == Disposable.PER_DISH else [])
    return disposable


# ---------- list ----------

def takeout_orders(since=None):
    since = since or timezone.now() - datetime.timedelta(days=30)
    return Order.objects.filter(timestamp__gte=since, voided_at__isnull=True).filter(Q(to_go_order=True) | Q(channel="delivery")).count()


def disposable_list(query="", show="all"):
    archived = show == "archived"
    items = list(Disposable.objects.filter(ingredient__archived=archived).select_related("ingredient__supplier").prefetch_related("dishes")
                 .order_by("ingredient__title", "size"))
    q = (query or "").strip().lower()
    if q:
        items = [d for d in items if q in d.title.lower() or (d.ingredient.supplier and q in d.ingredient.supplier.name.lower())]
    numbers = usage()
    types = dict(Disposable.ORDER_TYPES)
    bins = dict(Disposable.BINS)
    materials = dict(Disposable.MATERIALS)
    rows = []
    for d in items:
        i = d.ingredient
        on_hand, par = i.inventory or 0, i.par or 0
        state = ingredient_page.stock_state(on_hand, par)
        pct = ingredient_page.change_pct(i)
        used = numbers.get(d.id, {"used": 0, "spent": Decimal("0"), "charged": Decimal("0")})
        days = math.floor(on_hand / (used["used"] / 30)) if used["used"] else None
        sub = " · ".join(filter(None, [i.supplier.name if i.supplier_id else "",
                                       _("%(price)s for %(n)s") % {"price": money(i.pack_price), "n": _num(i.pack_size)} if i.pack_price is not None else ""]))
        rows.append({
            "d": d, "sub": sub or _("No price yet"), "material": materials[d.material], "bin": d.bin, "bin_label": bins[d.bin],
            "cost": money3(i.cost) if i.cost is not None else "—", "change": pct,
            "change_text": ("▲ " if pct > 0 else "▼ ") + f"{abs(pct):.1f}%" if abs(pct) >= 0.5 else "",
            "rule": rule_text(d, list(d.dishes.all())), "types": [types[t] for t in d.order_types if t in types],
            "fee": _("%(price)s fee") % {"price": money(d.fee_price)} if d.fee else "",
            "stock": f"{_num(on_hand)} / {_num(par)}" if par else f"{_num(on_hand)} / —", "state": state,
            "tag": {"low": _("Way under par"), "under": _("Under par"), "none": _("No par"), "ok": ""}[state], "bar": ingredient_page.bar(on_hand, par),
            "days": _("Not used lately") if days is None else ngettext("About %(n)d day left", "About %(n)d days left", days) % {"n": days},
            "used": used, "value": Decimal(str(on_hand)) * (i.cost or 0),
        })
    filters = [("all", _("All"), lambda r: True), ("low", _("Under par"), lambda r: r["state"] in ("low", "under")),
               ("compost", _("Compostable"), lambda r: r["bin"] == "compost"), ("notcompost", _("Not compostable"), lambda r: r["bin"] != "compost"),
               ("request", _("On request"), lambda r: r["d"].counted == Disposable.ON_REQUEST), ("fee", _("Charged to customer"), lambda r: r["d"].fee)]
    tests = {key: test for key, _label, test in filters}
    if archived:
        chips = [{"key": "all", "label": _("All"), "count": Disposable.objects.filter(ingredient__archived=False).count()}]
        shown = rows
    else:
        show = show if show in tests else "all"
        chips = [{"key": key, "label": label, "count": sum(1 for r in rows if test(r))} for key, label, test in filters]
        shown = [r for r in rows if tests[show](r)]
    archived_count = Disposable.objects.filter(ingredient__archived=True).count()
    if archived_count:
        chips.append({"key": "archived", "label": _("Archived"), "count": archived_count})
    groups = [{"name": name, "count": len(m), "rows": m} for key, name in Disposable.CATEGORIES if (m := [r for r in shown if r["d"].category == key])]

    everything = rows if not archived else []
    takeout = takeout_orders()
    since = timezone.now() - datetime.timedelta(days=30)
    takeout_spend = DisposableUse.objects.filter(order__timestamp__gte=since, order__voided_at__isnull=True).filter(
        Q(order__to_go_order=True) | Q(order__channel="delivery")).values_list("quantity", "cost")
    spend = sum((Decimal(q) * c for q, c in takeout_spend), Decimal("0"))
    used_all = sum(r["used"]["used"] for r in everything)
    composted = sum(r["used"]["used"] for r in everything if r["bin"] == "compost")
    under = [r for r in everything if r["state"] in ("low", "under")]
    kpis = [
        {"label": _("Packaging per takeout order"), "value": money(spend / takeout) if takeout else "$0.00",
         "sub": ngettext("Average over %(n)s pickup and delivery order", "Average over %(n)s pickup and delivery orders", takeout) % {"n": f"{takeout:,}"}},
        {"label": _("Stock value"), "value": money(sum((r["value"] for r in everything), Decimal("0"))),
         "sub": ngettext("%(n)d item on hand, at cost", "%(n)d items on hand, at cost", len(everything)) % {"n": len(everything)}},
        {"label": _("Compostable"), "value": f"{round(composted / used_all * 100)}%" if used_all else "—", "good": True,
         "sub": _("Of everything handed out, last 30 days")},
        {"label": _("Under par"), "value": str(len(under)), "bad": bool(under), "sub": ", ".join(r["d"].title for r in under) or _("All stocked")},
    ]
    return {"groups": groups, "filters": chips, "show": "archived" if archived else show, "query": (query or "").strip(), "kpis": kpis}


# ---------- editor ----------

def editor(disposable, draft, leave_ask=False):
    issue = problems(draft, disposable)
    dirty = is_dirty(draft, disposable)
    each = cost_each(draft)
    types = dict(Disposable.ORDER_TYPES)
    on_hand, par = float(_dec(draft["inventory"]) or 0), float(_dec(draft["par"]) or 0)
    state = ingredient_page.stock_state(on_hand, par)
    case = _dec(draft["case_size"]) or Decimal("0")
    supplier = draft["supplier"].strip() or _("the supplier")
    if state == "none":
        stock_msg = _("No par set")
    elif state == "ok":
        stock_msg = _("At or above par")
    else:
        stock_msg = _("%(n)s short") % {"n": _num(par - on_hand)}
    if not par:
        par_hint = _("Set a par level to get reorder alerts.")
    elif par > on_hand and case > 0:
        cases = math.ceil((par - on_hand) / float(case))
        par_hint = ngettext("Below par: %(n)d case from %(supplier)s covers it.", "Below par: %(n)d cases from %(supplier)s cover it.", cases) % {"n": cases, "supplier": supplier}
    else:
        par_hint = _("Stocked. When it drops below par, reorder from %(supplier)s.") % {"supplier": supplier}
    warnings = []
    if not draft["order_types"]:
        warnings.append(_("Pick at least one order type, or it’s never counted."))
    if draft["counted"] == Disposable.PER_DISH and not draft["dishes"]:
        warnings.append(_("Pick the dishes that go out in it."))
    if draft["counted"] == Disposable.PER_GUEST and "catering" not in draft["order_types"]:
        warnings.append(_("Per guest only applies to catering orders. Turn on Catering above."))
    if draft["counted"] == Disposable.PER_GUEST:
        warnings.append(_("Catering isn't built yet, so per-guest items aren't counted for now."))

    lines = []
    c = each or Decimal("0")
    count = int(draft["count"])
    chosen = [types[t].lower() for t in draft["order_types"]]
    if draft["counted"] == Disposable.PER_ORDER and chosen:
        lines.append({"label": _("Adds to every %(types)s order") % {"types": _(" and ").join(chosen)}, "value": money3(c * count), "kind": ""})
    if draft["counted"] == Disposable.PER_GUEST:
        lines.append({"label": _("Adds per catering guest"), "value": money3(c * count), "kind": ""})
    if draft["counted"] == Disposable.PER_DISH and draft["dishes"]:
        lines.append({"label": _("Adds to each takeout dish it’s used for"), "value": money3(c), "kind": ""})
    if draft["counted"] == Disposable.ON_REQUEST:
        lines.append({"label": _("Only when someone asks"), "value": _("%(price)s each") % {"price": money3(c)}, "kind": ""})
    fee_price = _dec(draft["fee_price"]) or Decimal("0")
    if draft["fee"]:
        gap = fee_price - c
        lines.append({"label": _("Customer pays"), "value": money(fee_price), "kind": ""})
        lines.append({"label": _("Covers cost, plus") if gap >= 0 else _("Short of cost by"), "value": money(abs(gap)), "kind": "good" if gap >= 0 else "bad"})
    pct = ingredient_page.change_pct(each, disposable.ingredient.previous_cost) if each is not None and disposable.pk and disposable.ingredient.previous_cost else 0.0
    if abs(pct) < 0.5:
        change = {"text": _("No price change since last delivery"), "kind": ""}
    else:
        change = {"text": (_("▲ %(pct).1f%% since last delivery") if pct > 0 else _("▼ %(pct).1f%% cheaper than last delivery")) % {"pct": abs(pct)}
                  + " " + _("(was %(price)s)") % {"price": money3(disposable.ingredient.previous_cost)}, "kind": "up" if pct > 0 else "down"}
    used = usage().get(disposable.pk, {"used": 0, "spent": Decimal("0"), "charged": Decimal("0")}) if disposable.pk else {"used": 0, "spent": Decimal("0"), "charged": Decimal("0")}
    days = math.floor(on_hand / (used["used"] / 30)) if used["used"] else None
    stations_dishes = Dish.objects.filter(archived=False).select_related("station").order_by("station__position", "title")
    return {
        "disposable": disposable, "d": draft, "title": full_title(draft) or _("Untitled item"), "state": state,
        "chip": {"ok": _("Stocked"), "low": _("Way under par"), "under": _("Under par"), "none": _("No par set")}[state],
        "bin_label": dict(Disposable.BINS)[draft["bin"]],
        "status": issue or (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issue else "dirty" if dirty else "", "can_save": dirty and not issue, "leave_ask": leave_ask,
        "categories": Disposable.CATEGORIES, "materials": Disposable.MATERIALS,
        "bins": [{"key": k, "label": label, "color": BIN_LOOK[k]} for k, label in Disposable.BINS],
        "types": [{"key": k, "label": label, "on": k in draft["order_types"]} for k, label in Disposable.ORDER_TYPES],
        "modes": [{"key": k, "label": label, "hint": MODE_HINTS[k]} for k, label in Disposable.COUNTED],
        "dishes": [{"dish": dish, "on": str(dish.id) in draft["dishes"]} for dish in stations_dishes],
        "count_label": _("For each catering guest") if draft["counted"] == Disposable.PER_GUEST else _("For each order"),
        "less_off": count <= 1, "more_off": count >= MAX_COUNT,
        "ask_text": _("Need %(name)s?") % {"name": draft["title"].strip().lower() or _("this")}, "warnings": warnings,
        "suppliers": Supplier.objects.all(), "places": StorageLocation.objects.all(),
        "pack_line": _("%(price)s ÷ %(n)s units") % {"price": money(_dec(draft["case_price"]) or 0), "n": _num(case)} if case > 0 else _("Set units per case"),
        "cost_line": _("%(price)s each") % {"price": money3(each)} if each is not None else "—",
        "on_hand_line": _("%(n)s on hand · %(cases)s cases · worth %(money)s") % {
            "n": _num(on_hand), "cases": _num(Decimal(str(on_hand)) / case if case else 0), "money": money(Decimal(str(on_hand)) * c)},
        "stock_msg": stock_msg, "bar": ingredient_page.bar(on_hand, par), "par_hint": par_hint,
        "cost_big": money3(each) if each is not None else "—", "change": change, "cost_lines": lines,
        "stats": [{"value": f"{used['used']:,}", "label": _("Used")}, {"value": money(used["spent"]), "label": _("Spent")},
                  {"value": money(used["charged"]) if draft["fee"] else "—", "label": _("Charged back")}],
        "runway": _("Not used in the last 30 days.") if days is None else ngettext(
            "At this pace, what’s on hand lasts about %(n)d day.", "At this pace, what’s on hand lasts about %(n)d days.", days) % {"n": days},
    }
