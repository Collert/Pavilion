"""
What a catering order costs. Works on plain line dicts so the admin's editing draft, the saved order and the
snapshot of the lines before proposed changes all price the same way:

    {"id": "12" (a CateringLine pk, "" for a new line), "kind": "dish"|"service"|"condiment"|"disposable",
     "ref": 5 (the dish/service/condiment/disposable pk), "quantity": 20, "amount": 3 (hours or days for hourly and
     daily services, else 0), "price": "12.50" (each, kept on the line so later catalog changes don't move an
     order), "removed": False (struck out while editing)}

Dishes are charged their catering price for the quantity (Dish.catering_price), services through Service.quote
(minimums, gratuity, their own tax and deposit), condiments and disposables at the price on the line. Tax is
settings.TAX_RATE except for services set to no tax. Deposits are held on the card, not charged.
"""
from decimal import ROUND_CEILING, Decimal

from django.conf import settings
from django.utils.translation import gettext as _, ngettext

from pos_server.models import Condiment, Dish, Disposable
from services.models import Service

from .models import CateringLine

CENT = Decimal("0.01")
DISH, SERVICE, CONDIMENT, DISPOSABLE = CateringLine.DISH, CateringLine.SERVICE, CateringLine.CONDIMENT, CateringLine.DISPOSABLE
CUSTOM = (CONDIMENT, DISPOSABLE)  # priced by hand


def money(value) -> str:
    return f"${Decimal(value or 0).quantize(CENT):,}"


def tax_rate() -> Decimal:
    return Decimal(str(getattr(settings, "TAX_RATE", "0") or "0"))


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except ArithmeticError:
        return Decimal("0")


class Catalog:
    """The dishes, services, condiments and disposables lines point at, loaded once."""

    def __init__(self, *line_lists):
        wanted = {DISH: set(), SERVICE: set(), CONDIMENT: set(), DISPOSABLE: set()}
        for lines in line_lists:
            for line in lines or []:
                if line.get("kind") in wanted and line.get("ref"):
                    wanted[line["kind"]].add(int(line["ref"]))
        self.items = {
            DISH: Dish.objects.prefetch_related("catering_prices").in_bulk(wanted[DISH]),
            SERVICE: Service.objects.in_bulk(wanted[SERVICE]),
            CONDIMENT: Condiment.objects.select_related("component").in_bulk(wanted[CONDIMENT]),
            DISPOSABLE: Disposable.objects.select_related("ingredient").in_bulk(wanted[DISPOSABLE]),
        }

    def get(self, kind, ref):
        return self.items.get(kind, {}).get(int(ref or 0))


def name_of(kind, item) -> str:
    if item is None:
        return _("Removed item")
    return item.title if kind != CONDIMENT else item.component.title


def hourly(kind, item) -> bool:
    """Hourly and daily services count hours or days as well as people or units."""
    return kind == SERVICE and item is not None and item.pricing in (Service.HOUR, Service.DAY)


def counts_quantity(kind, item) -> bool:
    """Whether the line's quantity means anything (a flat fee, or hours of a non-staff service, is one of it)."""
    if kind != SERVICE or item is None:
        return True
    return item.counts_units or item.pricing in (Service.UNIT, Service.PERSON)


def service_quote(service, quantity, amount, price):
    """Service.quote for a catering line: people/units in `quantity`, hours or days in `amount`."""
    quantity, amount = max(int(quantity or 1), 1), max(int(amount or 1), 1)
    timed = service.pricing in (Service.HOUR, Service.DAY)
    if service.counts_units:
        return service.quote(amount if timed else 1, quantity, price)
    return service.quote(amount if timed else quantity, 1, price)


def suggested_price(kind, item) -> Decimal:
    """Where a hand-priced line starts: a condiment's extra price, a disposable at about twice its cost."""
    if kind == CONDIMENT:
        return item.price
    if kind == DISPOSABLE:
        cost = _dec(item.ingredient.cost)
        return max(Decimal("0.05"), (cost * 2 * 20).to_integral_value(ROUND_CEILING) / 20).quantize(CENT)
    if kind == DISH:
        return item.catering_price(1)
    return item.price


def catalog_price(kind, item, quantity) -> Decimal:
    """The price a dish or service line takes when it's added or its quantity changes."""
    if kind == DISH:
        return item.catering_price(int(quantity or 1))
    if kind == SERVICE:
        return item.price
    return suggested_price(kind, item)


def figure(line, catalog) -> dict:
    """One line's money: total (before tax), tax, deposit, kitchen minutes and the texts for showing it."""
    kind, item = line["kind"], catalog.get(line["kind"], line["ref"])
    quantity, amount, price = max(int(line.get("quantity") or 1), 1), int(line.get("amount") or 0), _dec(line.get("price"))
    out = {"name": name_of(kind, item), "item": item, "total": Decimal("0"), "tax": Decimal("0"), "deposit": Decimal("0"),
           "minutes": 0, "hourly": hourly(kind, item), "counts": counts_quantity(kind, item)}
    if item is None:
        out.update(unit="", qty_text=f"× {quantity}")
        return out
    if kind == SERVICE:
        quote = service_quote(item, quantity, amount, price)
        out.update(total=quote["subtotal"] + quote["gratuity"], tax=quote["tax"], deposit=quote["deposit"])
        each = {Service.HOUR: _("%(p)s an hour"), Service.DAY: _("%(p)s a day"), Service.FLAT: _("%(p)s flat"),
                Service.PERSON: _("%(p)s a guest"), Service.UNIT: _("%(p)s each")}[item.pricing] % {"p": money(price)}
        if item.counts_units and item.pricing != Service.UNIT:
            each = _("%(price)s each") % {"price": each}
        out["unit"] = each
    else:
        out["total"] = (price * quantity).quantize(CENT)
        out["tax"] = (out["total"] * tax_rate()).quantize(CENT)
        if kind == DISH:
            out["minutes"] = item.prep_minutes * quantity
            out["unit"] = _("%(p)s each") % {"p": money(price)}
        elif kind == CONDIMENT:
            out["unit"] = _("%(p)s each") % {"p": money(price)}
            if abs(price - item.price) >= CENT:
                out["unit"] += " · " + _("regular %(p)s") % {"p": money(item.price)}
        else:
            out["unit"] = _("%(p)s each · costs you %(c)s") % {"p": money(price), "c": money(_dec(item.ingredient.cost))}
    if out["hourly"] and (item.pricing == Service.HOUR or amount > 1):
        unit_word = _("h") if item.pricing == Service.HOUR else _("days")
        out["qty_text"] = (f"{quantity} × " if item.counts_units else "") + f"{max(amount, 1)} {unit_word}"
    elif out["hourly"] and not item.counts_units:
        out["qty_text"] = _("1 day")
    else:
        out["qty_text"] = f"× {quantity}" if out["counts"] else ""
    return out


def totals(lines, catalog) -> dict:
    """{food, services, extras, subtotal, tax, total, deposit, minutes, portions} for the lines not removed."""
    out = {key: Decimal("0") for key in ("food", "services", "extras", "tax", "deposit")}
    out.update(minutes=0, portions=0)
    for line in lines:
        if line.get("removed"):
            continue
        f = figure(line, catalog)
        group = "food" if line["kind"] == DISH else "services" if line["kind"] == SERVICE else "extras"
        out[group] += f["total"]
        out["tax"] += f["tax"]
        out["deposit"] += f["deposit"]
        out["minutes"] += f["minutes"]
        if line["kind"] == DISH:
            out["portions"] += int(line.get("quantity") or 0)
    out["subtotal"] = out["food"] + out["services"] + out["extras"]
    out["total"] = out["subtotal"] + out["tax"]
    return out


def lines_of(order) -> list[dict]:
    """An order's saved lines as dicts."""
    return [{"id": str(line.id), "kind": line.kind, "ref": line.ref, "quantity": line.quantity, "amount": line.amount,
             "price": str(line.price), "removed": False} for line in order.lines.all()]


def order_totals(order) -> dict:
    lines = lines_of(order)
    return totals(lines, Catalog(lines))


def _qty_change(line, catalog):
    return figure(line, catalog)["qty_text"].replace("× ", "")


def changes(before, after, catalog) -> list[list[str]]:
    """What changed from `before` to `after` (line dicts), as [[text, 'add'|'rm'|'ch'], ...] for the timeline."""
    out = []
    old = {line["id"]: line for line in before if line.get("id")}
    for line in after:
        f = figure(line, catalog)
        was = old.get(line.get("id")) if line.get("id") else None
        if line.get("removed"):
            if was:
                out.append([_("Removed %(name)s × %(n)s") % {"name": f["name"], "n": was["quantity"]}, "rm"])
            continue
        if not was:
            text = _("Added %(name)s %(qty)s") % {"name": f["name"], "qty": f["qty_text"]}
            if line["kind"] in CUSTOM:
                text = _("%(text)s at %(price)s each") % {"text": text.strip(), "price": money(line["price"])}
            out.append([text.strip(), "add"])
            continue
        if int(was["quantity"]) != int(line["quantity"]) or int(was.get("amount") or 0) != int(line.get("amount") or 0):
            out.append([f"{f['name']} {_qty_change(was, catalog)} → {_qty_change(line, catalog)}", "ch"])
        if line["kind"] in CUSTOM and _dec(was["price"]) != _dec(line["price"]):
            out.append([f"{f['name']} {money(was['price'])} → {money(line['price'])} " + _("each"), "ch"])
    kept = {line.get("id") for line in after}
    for line in before:
        if line.get("id") not in kept:
            out.append([_("Removed %(name)s × %(n)s") % {"name": figure(line, catalog)["name"], "n": line["quantity"]}, "rm"])
    return out


def kitchen_text(minutes) -> str:
    hours, rest = divmod(int(round(minutes / 5) * 5), 60)
    if hours and rest:
        return _("%(h)d h %(m)d min") % {"h": hours, "m": rest}
    if hours:
        return _("%(h)d h") % {"h": hours}
    return ngettext("%(m)d min", "%(m)d min", rest) % {"m": rest}
