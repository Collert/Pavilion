"""
Services (design: AdminServices, list + editor). Services and their bookings live in the `services` app; this builds
the list (sections by category) and the editor, which works like the combo editor: one form, a draft posted back to
show the quote, saved on Save.
"""
import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _, ngettext

from services.models import Booking, Service

CENT = Decimal("0.01")

# category: (hint, icon path, tint background, tint ink)
LOOK = {
    Service.STAFF: (_("Servers, bartenders, chefs on site"), "M12 4a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z M5 20a7 7 0 0 1 14 0", "#E3EEF6", "#2D5A84"),
    Service.EQUIPMENT: (_("Chafing dishes, urns, tables, grills"), "M4 10h16v9H4z M8 10V6h8v4 M4 14h16", "#FDEBD0", "#8A4B00"),
    Service.VENUE: (_("Buyouts, private room, patio"), "M3 11 12 4l9 7 M6 10v10h12V10 M10 20v-5h4v5", "#DCEBE2", "#1F4A3A"),
    Service.DELIVERY: (_("Drop-off, setup, teardown"), "M3 7h11v9H3z M14 10h4l3 3v3h-7 M7 19a2 2 0 1 0 0-.01z M17 19a2 2 0 1 0 0-.01z", "#F1F4F2", "#3D4D45"),
    Service.EXPERIENCE: (_("Classes, tastings, chef’s table"), "M12 3l2.5 5 5.5.8-4 3.9.9 5.5L12 15.6 7.1 18.2l.9-5.5-4-3.9 5.5-.8z", "#FBE9F1", "#9B2C5E"),
    Service.EXTRA: (_("Cakes, printed menus, decor"), "M4 11h16v9H4z M4 7h16v4H4z M12 7v13 M12 7c-2-3-6-3-6 0 M12 7c2-3 6-3 6 0", "#F6F1E8", "#6B4E1F"),
}
PRICE_LABEL = {Service.FLAT: _("Flat fee"), Service.HOUR: _("Rate per hour"), Service.PERSON: _("Price per guest"),
               Service.DAY: _("Rate per day"), Service.UNIT: _("Price per item")}
UNIT_WORD = {Service.HOUR: _("hour"), Service.PERSON: _("guest"), Service.DAY: _("day"), Service.UNIT: _("item")}
MIN_UNIT = {Service.HOUR: _("hours"), Service.PERSON: _("guests"), Service.DAY: _("days"), Service.UNIT: _("items")}
QUOTE_LABEL = {Service.HOUR: _("Hours"), Service.PERSON: _("Guests"), Service.DAY: _("Days"), Service.UNIT: _("Items")}


def details_card(category):
    """(title, subtitle, [(key, label, unit, step, prefix)]) for a category's details card."""
    return {
        Service.STAFF: (_("Staffing details"), _("How many people you can send and how tips are handled"),
                        [("staff", _("People on the roster"), _("people"), "1", ""), ("gratuity", _("Automatic gratuity"), "%", "1", "")]),
        Service.EQUIPMENT: (_("Equipment details"), _("Stock you own and what happens to it while it’s out"),
                            [("owned", _("Units you own"), _("units"), "1", "")]),
        Service.VENUE: (_("Venue details"), _("Capacity and what the booking includes"),
                        [("seated", _("Seated capacity"), _("guests"), "1", ""), ("standing", _("Standing capacity"), _("guests"), "1", ""),
                         ("min_spend", _("Food and drink minimum"), "", "50", "$"), ("hours", _("Included hours"), _("hours"), "1", "")]),
        Service.DELIVERY: (_("Delivery details"), _("How far you go and what extra distance costs"),
                           [("radius", _("Covers up to"), _("km"), "1", ""), ("per_km", _("Each extra km"), "", "0.5", "$")]),
        Service.EXPERIENCE: (_("Experience details"), _("Group size and how long it runs"),
                             [("capacity", _("Up to"), _("guests"), "1", ""), ("minutes", _("Runs for"), _("minutes"), "15", "")]),
        Service.EXTRA: (_("Extra details"), _("Sold alongside an order or booking"), []),
    }[category]


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


def money(value):
    """$45, $2.50, $1,800: cents only when there are some."""
    value = Decimal(value or 0).quantize(CENT)
    return f"${value:,.0f}" if value == value.to_integral_value() else f"${value:,.2f}"


def money2(value):
    return f"${Decimal(value or 0).quantize(CENT):,.2f}"


def _num(value):
    """A detail for a form field: 15 rather than 15.0."""
    value = _dec(value) or Decimal("0")
    return f"{value.normalize():f}" if value != value.to_integral_value() else str(int(value))


def amount_text(pricing, n):
    return Service(pricing=pricing).amount_text(n)


def price_text(pricing, price):
    """'$45 / day', '$1,800'."""
    return money(price) + ("" if pricing == Service.FLAT else " / " + str(UNIT_WORD[pricing]))


def price_sub(pricing, minimum, category, min_spend):
    bits = []
    if pricing != Service.FLAT and minimum:
        bits.append(_("min %(amount)s") % {"amount": amount_text(pricing, minimum)})
    if category == Service.VENUE and min_spend:
        bits.append(_("%(price)s food and drink minimum") % {"price": money(min_spend)})
    if pricing == Service.FLAT and not bits:
        bits.append(_("flat fee"))
    return " · ".join(bits)


def tax_label(key):
    from pos_server.pricing import tax_rate
    rate = tax_rate() * 100
    return _("Store tax rate · %(rate)s%%") % {"rate": f"{rate.normalize():f}"} if key == Service.STORE_TAX else _("No tax")


def booked_30_days(since=None):
    """{service id: bookings made in the last 30 days that weren't declined or cancelled}."""
    since = since or timezone.now() - datetime.timedelta(days=30)
    out = {}
    for service_id in Booking.objects.filter(created_at__gte=since, status__in=Booking.LIVE).values_list("service_id", flat=True):
        out[service_id] = out.get(service_id, 0) + 1
    return out


# ---------- drafts ----------

def new_service():
    """An unsaved service for the New service editor (created on Save)."""
    return Service(title="", category=Service.STAFF, pricing=Service.HOUR, details=dict(Service.DETAILS[Service.STAFF]),
                   online=False, active=False)


def draft_from_service(service, qa=None, qb=None):
    if qa is None:
        qa = 1 if service.pricing == Service.FLAT else max(service.minimum, 1)
    if qb is None:
        qb = 2 if service.category == Service.EQUIPMENT else 1
    return {
        "title": service.title, "description": service.description, "category": service.category, "pricing": service.pricing,
        "price": _num(service.price), "minimum": str(service.minimum), "tax": service.tax,
        "details": {key: _num(service.details.get(key, default)) for key, default in Service.DETAILS[service.category].items()},
        "includes": [i for i in service.includes if i in {str(x) for x in Service.INCLUDES}],
        "deposit": service.deposit, "deposit_amount": _num(service.deposit_amount), "deposit_per": service.deposit_per,
        "lead_days": str(service.lead_days), "cancellation": service.cancellation, "needs_approval": service.needs_approval,
        "at_counter": service.at_counter, "online": service.online, "catering": service.catering, "active": service.active,
        "qa": str(qa), "qb": str(qb),
    }


def draft_from_post(post):
    category = post.get("category") if post.get("category") in Service.DETAILS else Service.STAFF
    pricing = post.get("pricing") if post.get("pricing") in dict(Service.PRICING) else Service.FLAT
    details = {key: post.get(f"detail-{key}", str(default)) for key, default in Service.DETAILS[category].items()}
    if post.get("category_was") and post.get("category_was") != category:
        # Picking another category starts its details and pricing over, like the design
        details = {key: str(default) for key, default in Service.DETAILS[category].items()}
        pricing = Service.CATEGORY_PRICING[category]
    draft = {
        "title": post.get("title", ""), "description": post.get("description", ""), "category": category, "pricing": pricing,
        "price": post.get("price", ""), "minimum": post.get("minimum", "0"),
        "tax": post.get("tax") if post.get("tax") in dict(Service.TAXES) else Service.STORE_TAX,
        "details": details, "includes": [i for i in post.getlist("includes") if i in {str(x) for x in Service.INCLUDES}],
        "deposit": bool(post.get("deposit")), "deposit_amount": post.get("deposit_amount", "0"),
        "deposit_per": Service.PER_BOOKING if post.get("deposit_per") == Service.PER_BOOKING else Service.PER_UNIT,
        "lead_days": post.get("lead_days", "0"),
        "cancellation": post.get("cancellation") if post.get("cancellation") in dict(Service.CANCELLATION) else Service.MODERATE,
        "needs_approval": bool(post.get("needs_approval")), "at_counter": bool(post.get("at_counter")),
        "online": bool(post.get("online")), "catering": bool(post.get("catering")), "active": bool(post.get("active")),
        "qa": post.get("qa", "1"), "qb": post.get("qb", "1"),
    }
    if draft["deposit"] and not (_dec(draft["deposit_amount"]) or 0) > 0 and post.get("deposit_was") != "1":
        # Turning the deposit on suggests about two days' rent, rounded to $5 (at least $25)
        price = _dec(draft["price"]) or Decimal("0")
        draft["deposit_amount"] = str(max(25, int((price * 2 / 5).quantize(Decimal("1"))) * 5))
    return draft


def problems(draft):
    """What stops Save, as a status line (None when it can be saved)."""
    price = _dec(draft["price"])
    if not draft["title"].strip() or price is None or price <= 0:
        return _("Needs a name and a price")
    if _int(draft["minimum"]) is None or _int(draft["minimum"]) < 0 or _int(draft["lead_days"]) is None or _int(draft["lead_days"]) < 0:
        return _("Check the minimum and the notice")
    if any(_dec(v) is None or _dec(v) < 0 for v in draft["details"].values()):
        return _("Check the details")
    if draft["deposit"] and draft["category"] == Service.EQUIPMENT and (_dec(draft["deposit_amount"]) is None or _dec(draft["deposit_amount"]) <= 0):
        return _("Set the deposit amount")
    return None


def _canonical(draft):
    return (draft["title"].strip(), draft["description"].strip(), draft["category"], draft["pricing"], _dec(draft["price"]),
            _int(draft["minimum"]) if draft["pricing"] != Service.FLAT else 0, draft["tax"],
            tuple(sorted((k, _dec(v)) for k, v in draft["details"].items())),
            tuple(sorted(draft["includes"])) if draft["category"] == Service.VENUE else (),
            (draft["deposit"], _dec(draft["deposit_amount"]), draft["deposit_per"]) if draft["category"] == Service.EQUIPMENT and draft["deposit"] else (False,),
            _int(draft["lead_days"]), draft["cancellation"], draft["needs_approval"], draft["at_counter"], draft["online"],
            draft["catering"], draft["active"])


def is_dirty(draft, service):
    return _canonical(draft) != _canonical(draft_from_service(service))


def apply_draft(draft, service):
    """Copy the draft onto `service` (not saved)."""
    service.title = draft["title"].strip()[:140]
    service.description = draft["description"].strip()
    service.category, service.pricing, service.tax = draft["category"], draft["pricing"], draft["tax"]
    service.price = (_dec(draft["price"]) or Decimal("0")).quantize(CENT)
    service.minimum = max(_int(draft["minimum"]) or 0, 0) if draft["pricing"] != Service.FLAT else 0
    service.details = {k: float(_dec(v) or 0) if (_dec(v) or 0) % 1 else int(_dec(v) or 0) for k, v in draft["details"].items()}
    service.includes = list(draft["includes"]) if draft["category"] == Service.VENUE else []
    equipment = draft["category"] == Service.EQUIPMENT
    service.deposit = equipment and draft["deposit"]
    service.deposit_amount = (_dec(draft["deposit_amount"]) or Decimal("0")).quantize(CENT) if service.deposit else Decimal("0")
    service.deposit_per = draft["deposit_per"]
    service.lead_days = max(_int(draft["lead_days"]) or 0, 0)
    service.cancellation, service.needs_approval = draft["cancellation"], draft["needs_approval"]
    service.at_counter, service.online, service.catering, service.active = draft["at_counter"], draft["online"], draft["catering"], draft["active"]
    return service


def save_draft(draft, service):
    apply_draft(draft, service).save()
    return service


# ---------- list ----------

def _tags(service, booked=None):
    tags = []
    if service.category == Service.EQUIPMENT:
        tags.append({"label": _("%(n)d owned") % {"n": int(service.detail("owned"))}, "kind": ""})
        if service.deposit:
            tags.append({"label": (_("%(price)s deposit each") if service.deposit_per == Service.PER_UNIT else _("%(price)s deposit"))
                         % {"price": money(service.deposit_amount)}, "kind": "amber"})
    elif service.category == Service.STAFF:
        tags.append({"label": _("%(n)d on the roster") % {"n": int(service.detail("staff"))}, "kind": ""})
    elif service.category == Service.VENUE:
        tags.append({"label": _("%(seated)d seated · %(standing)d standing") % {"seated": int(service.detail("seated")), "standing": int(service.detail("standing"))}, "kind": ""})
    elif service.category == Service.EXPERIENCE:
        tags.append({"label": _("Up to %(n)d guests") % {"n": int(service.detail("capacity"))}, "kind": ""})
    if service.needs_approval:
        tags.append({"label": _("Needs approval"), "kind": "blue"})
    if service.lead_days >= 7:
        tags.append({"label": _("%(n)d days notice") % {"n": service.lead_days}, "kind": ""})
    return tags


def service_list(query="", category="all", show=""):
    archived = show == "archived"
    services = list(Service.objects.filter(archived=archived))
    q = (query or "").strip().lower()
    if q:
        services = [s for s in services if q in s.title.lower() or q in s.description.lower()]
    category = category if category in Service.DETAILS else "all"
    booked = booked_30_days()
    names = dict(Service.CATEGORIES)
    chips = [{"key": "all", "label": _("All"), "count": len(services), "dot": ""}]
    chips += [{"key": key, "label": names[key], "count": sum(1 for s in services if s.category == key), "dot": LOOK[key][3]} for key in Service.DETAILS]
    sections = []
    for key in Service.DETAILS:
        if category not in ("all", key):
            continue
        rows = [s for s in services if s.category == key]
        if not rows:
            continue
        hint, path, bg, ink = LOOK[key]
        sections.append({
            "key": key, "name": names[key], "hint": hint, "icon": path, "bg": bg, "ink": ink,
            "sub": ngettext("%(n)d service", "%(n)d services", len(rows)) % {"n": len(rows)},
            "rows": [{"service": s, "price": price_text(s.pricing, s.price),
                      "price_sub": price_sub(s.pricing, s.minimum, s.category, s.detail("min_spend")),
                      "tags": _tags(s), "booked": booked.get(s.id, 0)} for s in rows],
        })
    archived_count = Service.objects.filter(archived=True).count()
    return {"chips": chips, "sections": sections, "category": category, "query": (query or "").strip(),
            "show": "archived" if archived else "", "archived": archived_count}


# ---------- editor ----------

def _quote(draft):
    """The quote preview: inputs, lines and notes for the draft as it stands."""
    service = apply_draft(draft, Service())
    qa, qb = max(_int(draft["qa"]) or 1, 1), max(_int(draft["qb"]) or 1, 1)
    q = service.quote(qa, qb)
    inputs = []
    if service.pricing in QUOTE_LABEL:
        inputs.append({"name": "qa", "label": QUOTE_LABEL[service.pricing], "value": qa})
    if service.counts_units:
        inputs.append({"name": "qb", "label": _("People") if service.category == Service.STAFF else _("Units"), "value": qb})
    base = money(service.price)
    if service.pricing != Service.FLAT:
        base += " × " + service.amount_text(q["quantity"])
    if q["count"] > 1:
        base += f" × {q['count']}"
    lines = [{"label": base, "value": money2(q["subtotal"]), "kind": ""}]
    if q["gratuity"]:
        lines.append({"label": _("Gratuity %(pct)s%%") % {"pct": _num(service.detail("gratuity"))}, "value": money2(q["gratuity"]), "kind": ""})
    if q["rate"]:
        lines.append({"label": _("Tax %(pct)s%%") % {"pct": f"{(q['rate'] * 100).normalize():f}"}, "value": money2(q["tax"]), "kind": ""})
    lines.append({"label": _("Total"), "value": money2(q["total"]), "kind": "big"})
    if q["deposit"]:
        lines.append({"label": _("Deposit held, refundable"), "value": money2(q["deposit"]), "kind": "muted"})
    if q["min_spend"]:
        lines.append({"label": _("Plus food and drink minimum"), "value": money2(q["min_spend"]), "kind": "muted"})
    return {"inputs": inputs, "lines": lines, "notes": q["notes"]}


def _bookings(service, draft):
    if not service.pk:
        return []
    out = []
    upcoming = service.bookings.filter(date__gte=timezone.localdate(), status__in=Booking.LIVE).order_by("date", "id")[:3]
    for b in upcoming:
        if draft["pricing"] == Service.FLAT:
            what = b.notes.splitlines()[0] if b.notes.strip() else b.get_channel_display()
        else:
            what = service.amount_text(b.amount)
            if service.category == Service.STAFF:
                what += " · " + ngettext("%(n)d person", "%(n)d people", b.count) % {"n": b.count}
            elif service.category == Service.EQUIPMENT:
                what += " · " + ngettext("%(n)d unit", "%(n)d units", b.count) % {"n": b.count}
        out.append({"mon": date_format(b.date, "M").upper(), "day": b.date.day, "who": b.name, "what": what,
                    "pending": b.status == Booking.PENDING, "state": b.get_status_display()})
    return out


def editor(service, draft, leave_ask=False):
    issue = problems(draft)
    dirty = is_dirty(draft, service)
    names = dict(Service.CATEGORIES)
    title, sub, fields = details_card(draft["category"])
    deposit = _dec(draft["deposit_amount"]) or Decimal("0")
    if draft["deposit_per"] == Service.PER_UNIT:
        deposit_line = _("Renting 3 units holds %(price)s. Not taxed, and not counted as sales.") % {"price": money2(deposit * 3)}
    else:
        deposit_line = _("Each booking holds %(price)s, however many units. Not taxed, and not counted as sales.") % {"price": money2(deposit)}
    min_spend = _dec(draft["details"].get("min_spend", 0)) or 0
    sub_line = price_sub(draft["pricing"], _int(draft["minimum"]) or 0, draft["category"], min_spend)
    return {
        "service": service, "d": draft, "title": draft["title"].strip() or _("Untitled service"),
        "status": issue or (_("Unsaved changes") if dirty else _("All changes saved")),
        "status_kind": "bad" if issue else "dirty" if dirty else "", "can_save": dirty and not issue, "leave_ask": leave_ask,
        "categories": [{"key": k, "label": names[k], "hint": LOOK[k][0], "icon": LOOK[k][1], "bg": LOOK[k][2], "ink": LOOK[k][3]} for k in Service.DETAILS],
        "pricings": Service.PRICING, "price_label": PRICE_LABEL[draft["pricing"]], "min_unit": MIN_UNIT.get(draft["pricing"], ""),
        "taxes": [{"key": k, "label": tax_label(k)} for k, _label in Service.TAXES],
        "price_line": _("Customers see: %(price)s") % {"price": price_text(draft["pricing"], _dec(draft["price"]) or 0)} + (f" · {sub_line}" if sub_line else ""),
        "detail_title": title, "detail_sub": sub,
        "fields": [{"key": k, "label": label, "unit": unit, "step": step, "pre": pre, "value": draft["details"].get(k, "0")} for k, label, unit, step, pre in fields],
        "includes": [{"label": str(i), "on": str(i) in draft["includes"]} for i in Service.INCLUDES],
        "deposit_line": deposit_line, "cancellations": Service.CANCELLATION,
        "channels": [{"key": k, "label": label, "on": draft[k]} for k, label in [
            ("active", _("Active · can be booked at all")), ("at_counter", _("At the counter (POS)")),
            ("online", _("Online booking")), ("catering", _("Add to catering orders"))]],
        "quote": _quote(draft), "bookings": _bookings(service, draft),
    }
