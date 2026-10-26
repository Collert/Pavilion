"""
Restocks (design: AdminRestocks, list + editor): purchase orders to suppliers for anything bought (ingredients,
including bought condiments and disposables). Draft, placed, delivered: saving one as delivered for the first time
adds each line's cases to stock and makes its case price the ingredient's pack price (the cost every component and
dish uses). The editor works like the others: one form, a draft posted back to show the sums, saved on Save; a
receipt file is kept as soon as it's picked.
"""
import datetime
import math
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from inventory.models import Restock, RestockLine
from pos_server.models import ComponentIngredient, Ingredient, Supplier

from . import ingredients as ingredient_page
from .units import fmt_qty

CENT = Decimal("0.01")
STATUS_LABEL = dict(Restock.STATUSES)


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


def _date(value):
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def money(value):
    return f"${Decimal(value or 0).quantize(CENT):,}"


def day(value):
    return date_format(value, "M j") if value else "—"


def kinds():
    """{ingredient id: "Condiment" | "Disposable"} (anything else is an ingredient)."""
    out = {i: _("Condiment") for i in ComponentIngredient.objects.filter(component__condiment__isnull=False).values_list("ingredient_id", flat=True)}
    out.update({i.id: _("Disposable") for i in Ingredient.objects.filter(disposable__isnull=False)})
    return out


def on_open_orders(exclude=None):
    """{ingredient id: restock id} for what's already on a draft or placed order."""
    lines = RestockLine.objects.filter(restock__status__in=[Restock.DRAFT, Restock.PLACED])
    if exclude:
        lines = lines.exclude(restock_id=exclude)
    return dict(lines.values_list("ingredient_id", "restock_id"))


def cases_needed(ingredient):
    pack = ingredient.pack_size or 1
    return max(1, math.ceil(((ingredient.par or 0) - (ingredient.inventory or 0)) / pack))


def under_par(exclude=None):
    """Ingredients below par with a supplier, not already on an open order."""
    taken = on_open_orders(exclude)
    return [i for i in Ingredient.objects.filter(archived=False, unlimited=False, supplier__isnull=False, par__gt=0).select_related("supplier")
            if (i.inventory or 0) < i.par and i.id not in taken]


def line_for(ingredient, cases=None):
    price = ingredient.pack_price if ingredient.pack_price is not None else Decimal("0")
    return {"id": "", "ingredient": str(ingredient.id), "cases": str(cases or 1), "price": f"{price:.2f}", "was": f"{price:.2f}",
            "pack": str(ingredient.pack_size or 1)}


# ---------- drafts ----------

def draft_from_restock(restock):
    iso = lambda d: d.isoformat() if d else ""  # noqa: E731
    return {
        "supplier": str(restock.supplier_id), "status": restock.status, "created_on": iso(restock.created_on),
        "placed_on": iso(restock.placed_on), "expected_on": iso(restock.expected_on), "delivered_on": iso(restock.delivered_on),
        "fee": f"{restock.fee:.2f}", "tax": f"{restock.tax:.2f}", "notes": restock.notes,
        "lines": [{"id": str(line.id), "ingredient": str(line.ingredient_id), "cases": str(line.cases), "price": f"{line.case_price:.2f}",
                   "was": f"{line.was_price:.2f}" if line.was_price is not None else f"{line.case_price:.2f}", "pack": str(line.pack_size)}
                  for line in restock.lines.all()] if restock.pk else [],
    }


def draft_from_post(post):
    count = min(_int(post.get("line-count")) or 0, 200)
    return {
        "supplier": post.get("supplier", ""), "status": post.get("status") if post.get("status") in STATUS_LABEL else Restock.DRAFT,
        "created_on": post.get("created_on", ""), "placed_on": post.get("placed_on", ""), "expected_on": post.get("expected_on", ""),
        "delivered_on": post.get("delivered_on", ""), "fee": post.get("fee", "0"), "tax": post.get("tax", "0"), "notes": post.get("notes", ""),
        "lines": [{"id": post.get(f"line-{i}-id", ""), "ingredient": post.get(f"line-{i}-ingredient", ""), "cases": post.get(f"line-{i}-cases", "1"),
                   "price": post.get(f"line-{i}-price", "0"), "was": post.get(f"line-{i}-was", "0"), "pack": post.get(f"line-{i}-pack", "1")}
                  for i in range(count)],
    }


def apply_op(draft, op, post, restock):
    """status:<status>, less:i, more:i, reset:i, remove_line:i, fill; an item picked in "+ Add an item" joins the order."""
    name, _sep, arg = (op or "").partition(":")
    lines = draft["lines"]
    i = _int(arg)
    line = lines[i] if i is not None and 0 <= i < len(lines) else None
    locked = bool(restock.received_at)
    today = timezone.localdate().isoformat()
    if name == "status" and arg in STATUS_LABEL and not locked:
        draft["status"] = arg
        if arg != Restock.DRAFT and not draft["placed_on"]:
            draft["placed_on"] = today
        if arg == Restock.DELIVERED and not draft["delivered_on"]:
            draft["delivered_on"] = today
        if arg != Restock.DELIVERED:
            draft["delivered_on"] = ""
        if arg == Restock.DRAFT:
            draft["placed_on"] = ""
    elif name == "less" and line and not locked:
        line["cases"] = str(max(1, (_int(line["cases"]) or 1) - 1))
    elif name == "more" and line and not locked:
        line["cases"] = str((_int(line["cases"]) or 0) + 1)
    elif name == "reset" and line:
        line["price"] = line["was"]
    elif name == "remove_line" and line and not locked:
        lines.pop(i)
    elif name == "fill" and not locked:
        have = {l["ingredient"] for l in lines}
        for ingredient in under_par(exclude=restock.pk):
            if str(ingredient.supplier_id) == draft["supplier"] and str(ingredient.id) not in have:
                lines.append(line_for(ingredient, cases_needed(ingredient)))
    added = Ingredient.objects.filter(pk=_int(post.get("add_line")), archived=False).first()
    if added and not locked and str(added.id) not in {l["ingredient"] for l in lines}:
        under = added.par and (added.inventory or 0) < added.par
        lines.append(line_for(added, cases_needed(added) if under else 1))
    return draft


def problems(draft, restock):
    if not Supplier.objects.filter(pk=_int(draft["supplier"])).exists():
        return _("Pick a supplier")
    if draft["status"] != Restock.DRAFT and not draft["lines"]:
        return _("Add at least one item first")
    if any((_int(l["cases"]) or 0) <= 0 or _dec(l["price"]) is None or _dec(l["price"]) < 0 for l in draft["lines"]):
        return _("Check the cases and prices")
    if _dec(draft["fee"]) is None or _dec(draft["tax"]) is None or _dec(draft["fee"]) < 0 or _dec(draft["tax"]) < 0:
        return _("Check the delivery fee and tax")
    return None


def _canonical(draft):
    return (draft["supplier"], draft["status"], draft["created_on"], draft["placed_on"], draft["expected_on"], draft["delivered_on"],
            _dec(draft["fee"]), _dec(draft["tax"]), draft["notes"].strip(),
            tuple((l["ingredient"], _int(l["cases"]), _dec(l["price"])) for l in draft["lines"]))


def is_dirty(draft, restock):
    return _canonical(draft) != _canonical(draft_from_restock(restock))


def _take_price(ingredient, line):
    """Make what this case cost the ingredient's pack price (scaled if its pack size changed since)."""
    old_cost = ingredient.cost
    pack = Decimal(str(line.pack_size or 1))
    ingredient.pack_price = (line.case_price * Decimal(str(ingredient.pack_size or 1)) / pack).quantize(CENT)
    new_cost = (ingredient.pack_price / Decimal(str(ingredient.pack_size or 1))).quantize(Decimal("0.000001"))
    if old_cost is not None and new_cost != old_cost:
        ingredient.previous_cost = old_cost
    ingredient.save()


@transaction.atomic
def save_draft(draft, restock, user=None):
    """Write the draft. Returns the number of lines whose stock was taken in (the first save as delivered)."""
    restock.supplier_id = _int(draft["supplier"])
    if not restock.received_at:
        restock.status = draft["status"]
    restock.created_on = _date(draft["created_on"]) or restock.created_on or timezone.localdate()
    restock.placed_on, restock.expected_on = _date(draft["placed_on"]), _date(draft["expected_on"])
    restock.delivered_on = _date(draft["delivered_on"]) if restock.status == Restock.DELIVERED else None
    restock.fee, restock.tax = _dec(draft["fee"]).quantize(CENT), _dec(draft["tax"]).quantize(CENT)
    restock.notes = draft["notes"].strip()
    if not restock.pk:
        restock.created_by = user
    restock.save()
    existing = {str(l.id): l for l in restock.lines.all()}
    keep, saved = set(), []
    for position, d in enumerate(draft["lines"]):
        line = existing.get(d["id"]) or RestockLine(restock=restock, ingredient_id=_int(d["ingredient"]))
        price_changed = line.pk and line.case_price != _dec(d["price"]).quantize(CENT)
        line.cases, line.position = _int(d["cases"]), position
        line.case_price = _dec(d["price"]).quantize(CENT)
        line.was_price = (_dec(d["was"]) or line.case_price).quantize(CENT)
        line.pack_size = float(_dec(d["pack"]) or 1)
        line.save()
        keep.add(line.id)
        saved.append((line, price_changed))
    if not restock.received_at:
        restock.lines.exclude(pk__in=keep).delete()
    taken = 0
    if restock.status == Restock.DELIVERED and not restock.received_at:
        for line, _changed in saved:
            Ingredient.objects.filter(pk=line.ingredient_id).update(inventory=F("inventory") + line.cases * line.pack_size)
            _take_price(Ingredient.objects.get(pk=line.ingredient_id), line)
            taken += 1
        restock.received_at = timezone.now()
        restock.save(update_fields=["received_at"])
    elif restock.received_at:
        # A price fixed after delivery: the ingredient follows when this is its latest delivery
        for line, changed in saved:
            latest = RestockLine.objects.filter(ingredient_id=line.ingredient_id, restock__received_at__isnull=False).order_by("-restock__received_at").first()
            if changed and latest and latest.pk == line.pk:
                _take_price(Ingredient.objects.get(pk=line.ingredient_id), line)
    return taken


def copy_as_draft(restock, user=None):
    """A new draft with the same items at today's prices."""
    new = Restock.objects.create(supplier=restock.supplier, notes=restock.notes, fee=restock.fee, created_by=user,
                                 expected_on=timezone.localdate() + datetime.timedelta(days=3))
    for position, line in enumerate(restock.lines.select_related("ingredient")):
        price = line.ingredient.pack_price if line.ingredient.pack_price is not None else line.case_price
        RestockLine.objects.create(restock=new, ingredient=line.ingredient, cases=line.cases, case_price=price, was_price=price,
                                   pack_size=line.ingredient.pack_size or 1, position=position)
    return new


def draft_order(supplier, user=None):
    """A new draft from a supplier with what's under par from them."""
    new = Restock.objects.create(supplier=supplier, created_by=user, expected_on=timezone.localdate() + datetime.timedelta(days=3))
    for position, ingredient in enumerate(i for i in under_par() if i.supplier_id == supplier.id):
        d = line_for(ingredient, cases_needed(ingredient))
        RestockLine.objects.create(restock=new, ingredient=ingredient, cases=int(d["cases"]), case_price=Decimal(d["price"]),
                                   was_price=Decimal(d["price"]), pack_size=float(d["pack"]), position=position)
    return new


# ---------- list ----------

def _changed(line):
    return line.was_price is not None and line.case_price != line.was_price


def who(user):
    return (user.get_full_name() or user.username) if user else ""


def restock_list(query="", show="all"):
    restocks = list(Restock.objects.select_related("supplier", "created_by").prefetch_related("lines__ingredient"))
    q = (query or "").strip().lower()
    if q:
        restocks = [r for r in restocks if q in r.number.lower() or q == str(r.id) or q in r.supplier.name.lower()
                    or any(q in l.ingredient.title.lower() for l in r.lines.all())]
    today = timezone.localdate()
    rows = []
    for r in restocks:
        lines = list(r.lines.all())
        names = [l.ingredient.title for l in lines]
        changes = sum(1 for l in lines if _changed(l))
        late = r.status == Restock.PLACED and r.expected_on and r.expected_on < today
        if r.status == Restock.DRAFT:
            date, sub = _("Started %(d)s") % {"d": day(r.created_on)}, _("Not sent yet")
        elif r.status == Restock.PLACED:
            date = _("Due %(d)s") % {"d": day(r.expected_on)}
            if not r.expected_on:
                sub = ""
            else:
                n = (r.expected_on - today).days
                sub = (ngettext("%(n)d day late", "%(n)d days late", -n) % {"n": -n} if n < 0 else _("Today") if n == 0 else _("Tomorrow") if n == 1
                       else _("In %(n)d days") % {"n": n})
        else:
            date, sub = _("Arrived %(d)s") % {"d": day(r.delivered_on)}, _("Placed %(d)s") % {"d": day(r.placed_on)}
        rows.append({"r": r, "date": date, "date_sub": sub, "late": late, "lines": lines,
                     "items": ngettext("%(n)d item", "%(n)d items", len(lines)) % {"n": len(lines)} + (" · " + ", ".join(names[:2]) + ("…" if len(names) > 2 else "") if names else ""),
                     "changes": changes, "by": _("By %(name)s") % {"name": who(r.created_by)} if r.created_by else "", "total": r.total})
    filters = [("all", _("All")), (Restock.DRAFT, _("Drafts")), (Restock.PLACED, _("Placed")), (Restock.DELIVERED, _("Delivered"))]
    show = show if show in dict(filters) else "all"
    chips = [{"key": key, "label": label, "count": sum(1 for row in rows if key == "all" or row["r"].status == key)} for key, label in filters]
    shown = [row for row in rows if show == "all" or row["r"].status == show]

    every = Restock.objects.prefetch_related("lines")
    placed = [r for r in every if r.status == Restock.PLACED]
    since = today - datetime.timedelta(days=30)
    recent = [r for r in every if r.status == Restock.DELIVERED and r.delivered_on and r.delivered_on >= since]
    changed_lines = [l for r in recent for l in r.lines.all() if _changed(l)]
    ups = sum(1 for l in changed_lines if l.case_price > l.was_price)
    drafts = sum(1 for r in every if r.status == Restock.DRAFT)
    no_receipt = sum(1 for r in every if r.status == Restock.DELIVERED and not r.receipt)
    upcoming = sorted(r.expected_on for r in placed if r.expected_on)
    kpis = [
        {"label": _("On the way"), "value": money(sum((r.total for r in placed), Decimal("0"))),
         "sub": ngettext("%(n)d placed order", "%(n)d placed orders", len(placed)) % {"n": len(placed)} + (_(" · next %(d)s") % {"d": day(upcoming[0])} if upcoming else "")},
        {"label": _("Spent · last 30 days"), "value": money(sum((r.total for r in recent), Decimal("0"))),
         "sub": ngettext("%(n)d delivery received", "%(n)d deliveries received", len(recent)) % {"n": len(recent)}},
        {"label": _("Price changes · 30 days"), "value": str(len(changed_lines)),
         "sub": _("%(up)d went up, %(down)d went down") % {"up": ups, "down": len(changed_lines) - ups}},
        {"label": _("Drafts"), "value": str(drafts),
         "sub": ngettext("%(n)d delivery is missing a receipt", "%(n)d deliveries are missing a receipt", no_receipt) % {"n": no_receipt} if no_receipt else _("Every delivery has a receipt")},
    ]
    low = under_par()
    by_supplier = {}
    for ingredient in low:
        by_supplier.setdefault(ingredient.supplier, []).append(ingredient)
    suggest = [{"supplier": supplier, "items": items, "sub": ngettext("%(n)d item", "%(n)d items", len(items)) % {"n": len(items)}
                + " · " + _("about %(money)s") % {"money": money(sum(((i.pack_price or 0) * cases_needed(i) for i in items), Decimal("0")))}}
               for supplier, items in sorted(by_supplier.items(), key=lambda kv: kv[0].name)]
    return {"rows": shown, "filters": chips, "show": show, "query": (query or "").strip(), "kpis": kpis, "suggest": suggest,
            "suggest_title": ngettext("%(n)d item is under par", "%(n)d items are under par", len(low)) % {"n": len(low)}}


# ---------- editor ----------

def editor(restock, draft, leave_ask=False):
    issue = problems(draft, restock)
    dirty = is_dirty(draft, restock)
    locked = bool(restock.received_at)
    supplier = Supplier.objects.filter(pk=_int(draft["supplier"])).first()
    type_of = kinds()
    ids = [_int(l["ingredient"]) for l in draft["lines"]]
    found = {i.id: i for i in Ingredient.objects.filter(pk__in=[i for i in ids if i]).select_related("supplier")}
    lines, subtotal, changes = [], Decimal("0"), []
    for n, l in enumerate(draft["lines"]):
        ingredient = found.get(_int(l["ingredient"]))
        if ingredient is None:
            continue
        cases, price, was = _int(l["cases"]) or 0, _dec(l["price"]) or Decimal("0"), _dec(l["was"]) or Decimal("0")
        pack = float(_dec(l["pack"]) or 1)
        unit = ingredient.unit_of_measurement
        changed = was > 0 and abs(price - was) >= CENT
        pct = float((price - was) / was * 100) if was else 0.0
        under = ingredient.par and (ingredient.inventory or 0) < ingredient.par
        total = price * cases
        subtotal += total
        lines.append({"n": n, "l": l, "ingredient": ingredient, "kind": type_of.get(ingredient.id, _("Ingredient")),
                      "sub": _("%(pack)s a case · %(total)s in total") % {"pack": fmt_qty(pack, unit), "total": fmt_qty(cases * pack, unit)}
                      + (_(" · under par") if under else ""),
                      "changed": changed, "up": pct > 0, "delta": ("▲ " if pct > 0 else "▼ ") + f"{abs(pct):.1f}%" if changed else _("Last paid"),
                      "was": money(was), "total": money(total)})
        if changed:
            per_old, per_new = was / Decimal(str(pack)), price / Decimal(str(pack))
            used = sorted({u["name"] for u in ingredient_page.uses(ingredient)})
            changes.append({"name": ingredient.title, "up": pct > 0,
                            "line": _("%(old)s → %(new)s a case (%(sign)s%(pct).1f%%) · %(a)s → %(b)s") % {
                                "old": money(was), "new": money(price), "sign": "+" if pct > 0 else "−", "pct": abs(pct),
                                "a": ingredient_page.cost_text(per_old, unit), "b": ingredient_page.cost_text(per_new, unit)},
                            "effect": _("Changes the cost of %(list)s") % {"list": ", ".join(used[:3]) + ("…" if len(used) > 3 else "")} if used else _("Changes its cost per unit")})
    fee, tax = _dec(draft["fee"]) or Decimal("0"), _dec(draft["tax"]) or Decimal("0")
    status = draft["status"]
    order = [Restock.DRAFT, Restock.PLACED, Restock.DELIVERED]
    at = order.index(status)
    steps = []
    for i, (key, label, about, icon) in enumerate([
            (Restock.DRAFT, _("Draft"), _("Still being put together"), "M4 20h4L19 9l-4-4L4 16z M13.5 6.5l4 4"),
            (Restock.PLACED, _("Placed"), _("Sent to the supplier"), "M3 11 21 3l-8 18-2-8z"),
            (Restock.DELIVERED, _("Delivered"), _("Received and counted"), "m5 12.5 4.5 4.5L19 7.5")]):
        sub = about
        if key == Restock.PLACED and draft["placed_on"]:
            sub = _("Sent %(d)s") % {"d": day(_date(draft["placed_on"]))}
        if key == Restock.DELIVERED and status == Restock.DELIVERED and draft["delivered_on"]:
            sub = _("Arrived %(d)s") % {"d": day(_date(draft["delivered_on"]))}
        steps.append({"key": key, "label": label, "sub": sub, "icon": icon, "on": key == status, "done": i < at, "off": locked and key != status})
    if status == Restock.DELIVERED:
        dates = [("placed_on", _("Placed on")), ("delivered_on", _("Delivered on"))]
    elif status == Restock.PLACED:
        dates = [("placed_on", _("Placed on")), ("expected_on", _("Expected"))]
    else:
        dates = [("created_on", _("Started")), ("expected_on", _("Want it by"))]
    supplier_name = supplier.name if supplier else _("the supplier")
    if locked:
        note, note_kind = _("Received %(d)s. Stock and prices were updated then. You can still fix prices and attach the receipt.") % {"d": day(restock.delivered_on)}, ""
    elif status == Restock.DELIVERED:
        note, note_kind = ngettext("When you save, %(n)d item is added to stock and the new prices flow into recipe and dish costs.",
                                   "When you save, %(n)d items are added to stock and the new prices flow into recipe and dish costs.", len(lines)) % {"n": len(lines)}, "go"
    elif status == Restock.PLACED:
        note, note_kind = _("Waiting on %(s)s. Mark it delivered when it arrives to add the stock.") % {"s": supplier_name}, ""
    else:
        note, note_kind = _("Drafts don’t change stock or prices."), ""
    in_order = {l["ingredient"] for l in draft["lines"]}
    catalog = [i for i in Ingredient.objects.filter(archived=False, unlimited=False).select_related("supplier").order_by("title") if str(i.id) not in in_order]
    pack_text = lambda i: _("%(pack)s a case") % {"pack": fmt_qty(i.pack_size or 1, i.unit_of_measurement)}  # noqa: E731
    from_supplier = [{"id": i.id, "label": f"{i.title} · {pack_text(i)}"} for i in catalog if supplier and i.supplier_id == supplier.id]
    others = [{"id": i.id, "label": f"{i.supplier.name if i.supplier_id else _('No supplier')} · {i.title} · {pack_text(i)}"} for i in catalog if not supplier or i.supplier_id != supplier.id]
    low_here = [i for i in under_par(exclude=restock.pk) if supplier and i.supplier_id == supplier.id and str(i.id) not in in_order]
    receipt = None
    if restock.receipt:
        name = restock.receipt.name.rsplit("/", 1)[-1]
        try:
            size = restock.receipt.size
            size_text = f"{size / 1048576:.1f} MB" if size > 1048576 else f"{max(1, round(size / 1024))} KB"
        except (OSError, ValueError):
            size_text = ""
        image = name.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "gif", "webp", "heic")
        receipt = {"name": name, "url": restock.receipt.url, "image": image, "kind": _("Photo") if image else "PDF", "size": size_text}
    removable = restock.status != Restock.DELIVERED
    return {
        "restock": restock, "d": draft, "title": restock.number, "supplier": supplier, "locked": locked,
        "status": issue or (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issue else "dirty" if dirty else "", "can_save": dirty and not issue, "leave_ask": leave_ask,
        "steps": steps, "dates": [{"name": key, "label": label, "value": draft[key]} for key, label in dates],
        "hidden_dates": [k for k in ("created_on", "placed_on", "expected_on", "delivered_on") if k not in dict(dates)],
        "suppliers": Supplier.objects.all(), "supplier_locked": bool(draft["lines"]) or locked,
        "note": note, "note_kind": note_kind, "lines": lines, "from_supplier": from_supplier, "others": others,
        "add_hint": ngettext("%(n)d more item usually comes from %(s)s", "%(n)d more items usually come from %(s)s", len(from_supplier)) % {"n": len(from_supplier), "s": supplier_name} if from_supplier else "",
        "fill_count": len(low_here), "fill_off": not low_here or locked,
        "subtotal": money(subtotal), "sub_label": ngettext("%(n)d item", "%(n)d items", len(lines)) % {"n": len(lines)},
        "total": money(subtotal + fee + tax), "receipt": receipt,
        "receipt_warn": _("Delivered without a receipt. Add one so the totals can be checked later.") if status == Restock.DELIVERED and not receipt else "",
        "changes": changes, "remove_text": _("Delivered orders stay on record") if not removable else _("Delete this order"), "removable": removable,
    }
