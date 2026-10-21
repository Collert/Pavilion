"""
Pricing for carts from the register and the online store.

The browser sends cart *lines*. Their prices are never trusted: everything is priced here from the database.

    {"type": "dish", "id": 5, "qty": 2, "cust": {...}, "note": "", "discount": <discount spec>}
    {"type": "combo", "id": 3, "qty": 1, "slots": {"<slot id>": [{"dish": 7, "cust": {...}}, ...]}, "cust": {...}}

`cust` is a dish's customization (every key is optional; leaving it out means "as standard"):

    {"parts": {"<DishComponent id>": steps},        adjustable parts: 0 = leave out, up to standard + max_extra
     "options": {"<group key>": <Option id>,         pick-one groups (see DishKit.groups for the keys)
                 "<group key>": {"<Option id>": n}}, pick-any groups: how many of each
     "condiments": {"<Extra id>": count},            on the side (see "Condiments" below)
     "allergies": ["Dairy", ...],                    told to the kitchen
     "note": "..."}

A part's steps are pieces when the dish uses whole pieces (8 pierogies), else whole amounts (see
DishComponent.step / .standard). A combo's slot lists its picks: a required one exactly `picks` of them (left
out = its default picks), an optional one up to `picks` (left out = none). A combo's own `cust` only uses
"condiments".

A discount spec is {"offer": <Discount id>}, {"code": "WELCOME10"} or {"manual": {"kind": "pct"|"amt",
"value": "10"}}. Lines take item discounts; the order takes one more on what is left after them.

Line prices: dish price (or Combo.unit_price of the picks: set price + upcharges, or percent off) + customizations. An adjustable part
costs its component's `extra_price` for every step above the standard; options cost their price each time. Tax (settings.TAX_RATE) is charged on the
total after discounts.

Condiments: an Extra whose component is a Condiment is priced per order, not per line. The first `free_per_order`
servings of it across the whole cart are free and each one after costs `Condiment.price`. The charge lands on the
lines in cart order; a line with a quantity above 1 gets the same count charged on each unit, rounding in the
customer's favour, so unit prices stay whole cents. The charged count per unit is kept in the line's selection
("charged": {component id: n}). Other extras (napkins) cost their own `Extra.price` each.
"""
import datetime
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from .models import ALLERGENS, Combo, ComboSlot, ComboSlotOption, Discount, Dish, DishComponent, Extra, OptionGroup

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_QTY = 99
NOTE_MAX = 140


class OrderError(Exception):
    """A cart that can't become an order. The message is safe to show to the customer or cashier."""


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_cents(amount: Decimal) -> int:
    return int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_money(value) -> Decimal:
    """Parse a user-supplied amount into a non-negative Decimal with 2 places."""
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError, TypeError):
        raise OrderError(_("Invalid amount: %(value)s") % {"value": value})
    if amount < 0 or not amount.is_finite():
        raise OrderError(_("Invalid amount: %(value)s") % {"value": value})
    return amount


def tax_rate() -> Decimal:
    return Decimal(str(getattr(settings, "TAX_RATE", "0")))


def _count(value, low, high, what):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise OrderError(_("The cart is malformed."))
    if not low <= number <= high:
        raise OrderError(_("%(what)s must be between %(low)s and %(high)s.") % {"what": what, "low": low, "high": high})
    return number


def _clean_note(value) -> str:
    return str(value or "").strip()[:NOTE_MAX]


# ---------- what can be changed about a dish ----------

def dish_queryset():
    """Dishes with everything DishKit reads, prefetched."""
    return Dish.objects.select_related("station").prefetch_related(
        Prefetch("dishcomponent_set", queryset=DishComponent.objects.select_related("component")
                 .prefetch_related("component__option_groups__options__component")),
        "option_groups__options__component",
        Prefetch("extras", queryset=Extra.objects.select_related("component__condiment")),
    )


def combo_queryset():
    return Combo.objects.prefetch_related(
        Prefetch("slots", queryset=ComboSlot.objects.prefetch_related(
            Prefetch("options", queryset=ComboSlotOption.objects.select_related("dish__station")))),
        Prefetch("extras", queryset=Extra.objects.select_related("component__condiment")),
    )


def condiment_of(extra):
    """The Condiment profile of an extra's component, or None (napkins, straws...)."""
    return getattr(extra.component, "condiment", None)


class DishKit:
    """
    The customization sheet of one dish: adjustable parts, modifier groups (the dish's own, then those of each of
    its components) and condiments.

    Group keys: "<group id>" for the dish's groups, "<group id>.<DishComponent id>" for a component's group
    (a component's group belongs to the part it adjusts, and disappears when that part is left out).
    """

    def __init__(self, dish):
        self.dish = dish
        self.parts = list(dish.dishcomponent_set.all())
        self.groups = []  # (key, group, DishComponent or None)
        for group in dish.option_groups.all():
            self.groups.append((str(group.id), group, None))
        for dc in self.parts:
            for group in dc.component.option_groups.all():
                self.groups.append((f"{group.id}.{dc.id}", group, dc))
        self.groups = [(key, group, dc) for key, group, dc in self.groups if list(group.options.all())]
        self.condiments = list(dish.extras.all())

    @property
    def customizable(self) -> bool:
        return bool(any(dc.adjustable for dc in self.parts) or self.groups or self.condiments)

    def as_json(self):
        """For the register's customization sheet. Money is in cents."""
        groups = []
        for key, group, dc in self.groups:
            default = group.default_option()
            groups.append({
                "key": key, "title": group.title, "note": group.note, "part": dc.id if dc else None,
                "pick": group.pick, "required": group.required, "max": OptionGroup.MAX_PICKS,
                "options": [{"id": o.id, "title": o.title, "price": to_cents(o.price), "available": o.available}
                            for o in group.options.all()],
                "default": default.id if default else None,
            })
        return {
            "parts": [{"id": dc.id, "title": dc.component.title, "extra": to_cents(dc.component.extra_price),
                       "standard": dc.standard, "max": dc.standard + dc.max_extra} for dc in self.parts if dc.adjustable],
            "groups": groups,
            "condiments": _extras_json(self.condiments),
        }


def _extras_json(extras):
    """`free` is the condiment's free servings per order (None: priced per line at `price`; 99+: always free)."""
    out = []
    for e in extras:
        condiment = condiment_of(e)
        out.append({"id": e.id, "title": e.label, "max": e.max_count, "available": e.available,
                    "price": to_cents(condiment.price if condiment else e.price),
                    "free": condiment.free_per_order if condiment else None})
    return out


@dataclass
class Picked:
    """A priced dish (standalone or in a combo) with its customizations."""
    dish: Dish
    extra: Decimal = ZERO                       # what customizations add to one
    mods: list = field(default_factory=list)    # readable lines for the kitchen
    selection: dict = field(default_factory=dict)
    use: Counter = field(default_factory=Counter)  # component id -> amount for one
    condiments: dict = field(default_factory=dict)  # component id -> [Condiment, servings for one], priced per order
    note: str = ""
    slot: str = ""
    upcharge: Decimal = ZERO                    # a combo pick's upcharge (set-price combos)


def _condiments_choice(raw, condiments, picked: Picked, owner_title):
    """Apply "condiments" from a customization to `picked`."""
    condiment_by_id = {str(e.id): e for e in condiments}
    counts = raw.get("condiments") or {}
    if not isinstance(counts, dict):
        raise OrderError(_("The cart is malformed."))
    for extra in condiments:
        count = counts.get(str(extra.id))
        if not count:
            continue
        count = _count(count, 0, extra.max_count, extra.label)
        if not extra.available:
            raise OrderError(_("%(extra)s is sold out.") % {"extra": extra.label})
        condiment = condiment_of(extra)
        if condiment:
            picked.condiments.setdefault(extra.component_id, [condiment, 0])[1] += count
        else:
            picked.extra += extra.price * count
        picked.use[extra.component_id] += extra.quantity * count
        picked.mods.append(extra.label if count == 1 else f"{count}× {extra.label}")
        picked.selection.setdefault("condiments", {})[str(extra.id)] = count
    unknown = set(map(str, counts)) - set(condiment_by_id)
    if unknown:
        raise OrderError(_("That condiment isn't available for %(item)s.") % {"item": owner_title})


def _pick_one(group, key, raw_options, dish):
    """The option chosen in a pick-one group (or its standard), or None."""
    if raw_options.get(key) in (None, ""):
        return group.default_option()
    chosen = next((o for o in group.options.all() if str(o.id) == str(raw_options[key])), None)
    if chosen is None:
        raise OrderError(_("That option isn't available for %(dish)s.") % {"dish": dish.title})
    return chosen


def _pick_any(group, key, raw_options, dish):
    """[(option, count)] chosen in a pick-any group."""
    raw = raw_options.get(key) or {}
    if not isinstance(raw, dict):
        raise OrderError(_("The cart is malformed."))
    by_id = {str(o.id): o for o in group.options.all()}
    if set(map(str, raw)) - set(by_id):
        raise OrderError(_("That option isn't available for %(dish)s.") % {"dish": dish.title})
    picks = []
    for option_id, option in by_id.items():
        count = raw.get(option_id)
        if count:
            count = _count(count, 0, OptionGroup.MAX_PICKS, option.title)
            if count:
                picks.append((option, count))
    return picks


def price_dish(kit: DishKit, raw) -> Picked:
    """Validate and price one dish's customization (`cust`). Returns a Picked for ONE dish."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise OrderError(_("The cart is malformed."))
    dish = kit.dish
    picked = Picked(dish=dish)

    # Parts: how many steps of each
    raw_parts = raw.get("parts") or {}
    if not isinstance(raw_parts, dict):
        raise OrderError(_("The cart is malformed."))
    steps = {}
    for dc in kit.parts:
        value = raw_parts.get(str(dc.id))
        steps[dc.id] = dc.standard if value is None or not dc.adjustable else \
            _count(value, 0, dc.standard + dc.max_extra, dc.component.title)

    # Modifier groups
    raw_options = raw.get("options") or {}
    if not isinstance(raw_options, dict):
        raise OrderError(_("The cart is malformed."))
    dish_multiplier = 1.0
    part_choices = {}  # DishComponent id -> [Option]
    option_mods = []
    for key, group, dc in kit.groups:
        if dc is not None and steps[dc.id] == 0:
            continue  # the part is left out, so its options don't apply
        if group.pick == OptionGroup.ANY:
            picks = _pick_any(group, key, raw_options, dish)
            if group.required and not picks:
                raise OrderError(_("Pick at least one %(group)s for %(dish)s.") % {"group": group.title.lower(), "dish": dish.title})
            for option, count in picks:
                if not option.available:
                    raise OrderError(_("%(option)s is sold out.") % {"option": option.title})
                picked.extra += option.price * count
                if option.component_id:
                    picked.use[option.component_id] += option.quantity * count
                option_mods.append("+ " + option.title if count == 1 else f"+ {count}× {option.title}")
            if picks:
                picked.selection.setdefault("options", {})[key] = {str(o.id): n for o, n in picks}
            continue
        chosen = _pick_one(group, key, raw_options, dish)
        if chosen is None:
            if group.required:
                raise OrderError(_("Pick the %(group)s for %(dish)s.") % {"group": group.title.lower(), "dish": dish.title})
            continue
        if not chosen.available:
            raise OrderError(_("%(option)s is sold out.") % {"option": chosen.title})
        picked.extra += chosen.price
        default = group.default_option()
        if default is None or chosen.id != default.id:
            option_mods.append(chosen.title)
            picked.selection.setdefault("options", {})[key] = chosen.id
        if dc is None:
            dish_multiplier *= chosen.multiplier
            if chosen.component_id:
                picked.use[chosen.component_id] += chosen.quantity
        else:
            part_choices.setdefault(dc.id, []).append(chosen)

    # Stock used, and the price and kitchen notes for parts
    for dc in kit.parts:
        count = steps[dc.id]
        component_id, multiplier = dc.component_id, dish_multiplier
        for option in part_choices.get(dc.id, []):
            multiplier *= option.multiplier
            if option.component_id:
                component_id = option.component_id
        if count:
            picked.use[component_id] += dc.step * count * multiplier
        if count != dc.standard:
            name = dc.component.title.lower()
            picked.mods.append(_("No %(part)s") % {"part": name} if count == 0 else f"{count}× {name}")
            picked.selection.setdefault("parts", {})[str(dc.id)] = count
            if count > dc.standard:
                picked.extra += dc.component.extra_price * (count - dc.standard)
    picked.mods.extend(option_mods)

    _condiments_choice(raw, kit.condiments, picked, dish.title)

    allergies = raw.get("allergies") or []
    if not isinstance(allergies, list) or any(a not in ALLERGENS for a in allergies):
        raise OrderError(_("The cart is malformed."))
    if allergies:
        allergies = list(dict.fromkeys(allergies))
        picked.mods.append(_("Allergy: %(list)s") % {"list": ", ".join(a.lower() for a in allergies)})
        picked.selection["allergies"] = allergies

    picked.note = _clean_note(raw.get("note"))
    return picked


# ---------- lines ----------

@dataclass
class AppliedDiscount:
    label: str
    kind: str
    value: Decimal
    amount: Decimal
    discount: Discount = None   # None for a manual discount

    @property
    def short(self):
        return f"{self.value.normalize():f}% off" if self.kind == Discount.PERCENT else f"${self.value:.2f} off"

    def as_json(self):
        return {"label": self.label, "short": self.short, "amount": to_cents(self.amount),
                "id": self.discount.id if self.discount else None,
                "code": self.discount.code if self.discount else None}


@dataclass
class PricedLine:
    kind: str                 # "dish" or "combo"
    item: object              # Dish or Combo
    quantity: int
    unit_price: Decimal
    picks: list               # [Picked]: the dish itself, or one per filled combo slot
    mods: list = field(default_factory=list)   # combo-level lines (left-out slots, combo extras)
    extra_picks: list = field(default_factory=list)  # a combo's own condiments (a Picked without a dish)
    selection: dict = field(default_factory=dict)
    note: str = ""
    discount: AppliedDiscount = None
    raw_discount: object = None
    discount_error: str = ""

    @property
    def condiments(self) -> dict:
        """component id -> [Condiment, servings for one unit of the line] (its picks' and the combo's own)."""
        out = {}
        for picked in self.picks + self.extra_picks:
            for component_id, (condiment, count) in picked.condiments.items():
                out.setdefault(component_id, [condiment, 0])[1] += count
        return out

    @property
    def gross(self) -> Decimal:
        return self.unit_price * self.quantity

    @property
    def net(self) -> Decimal:
        return self.gross - (self.discount.amount if self.discount else ZERO)


def _load(lines):
    """Fetch every dish and combo the lines mention, with their kits."""
    dish_ids, combo_ids = set(), set()
    for line in lines:
        if not isinstance(line, dict) or line.get("type") not in ("dish", "combo"):
            raise OrderError(_("The cart is malformed."))
        try:
            (dish_ids if line["type"] == "dish" else combo_ids).add(int(line["id"]))
        except (KeyError, TypeError, ValueError):
            raise OrderError(_("The cart is malformed."))
    combos = combo_queryset().in_bulk(combo_ids)
    if set(combos) != combo_ids:
        raise OrderError(_("Some items in your cart no longer exist. Please refresh the menu."))
    for combo in combos.values():
        for slot in combo.slots.all():
            dish_ids |= {option.dish_id for option in slot.options.all()}
    dishes = dish_queryset().in_bulk(dish_ids)
    if not {int(l["id"]) for l in lines if l["type"] == "dish"} <= set(dishes):
        raise OrderError(_("Some items in your cart no longer exist. Please refresh the menu."))
    kits = {dish_id: DishKit(dish) for dish_id, dish in dishes.items()}
    return kits, combos


def _price_line(line, kits, combos) -> PricedLine:
    quantity = _count(line.get("qty", 1), 1, MAX_QTY, _("Quantity"))
    if line["type"] == "dish":
        kit = kits[int(line["id"])]
        if not kit.dish.available:
            raise OrderError(_("%(dish)s is out of stock.") % {"dish": kit.dish.title})
        picked = price_dish(kit, line.get("cust"))
        note = picked.note or _clean_note(line.get("note"))
        picked.note = note
        return PricedLine("dish", kit.dish, quantity, kit.dish.price + picked.extra, [picked],
                          selection=picked.selection, note=note, raw_discount=line.get("discount"))

    combo = combos[int(line["id"])]
    if not combo.active or combo.archived:
        raise OrderError(_("%(combo)s isn't available.") % {"combo": combo.title})
    raw_slots = line.get("slots") or {}
    if not isinstance(raw_slots, dict):
        raise OrderError(_("The cart is malformed."))
    picks, chosen, mods, selection = [], [], [], {}
    for slot in combo.slots.all():
        options = list(slot.options.all())
        raw = raw_slots.get(str(slot.id))
        if raw is None:
            raw = [{"dish": o.dish_id} for o in slot.default_picks()]
        if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
            raise OrderError(_("The cart is malformed."))
        if len(raw) > slot.picks or (slot.required and len(raw) < slot.picks):
            raise OrderError(ngettext("Pick %(n)d %(slot)s for the %(combo)s.", "Pick %(n)d for the %(slot)s of the %(combo)s.", slot.picks)
                             % {"n": slot.picks, "slot": slot.title.lower(), "combo": combo.title})
        if slot.required and not options:
            raise OrderError(_("The %(combo)s isn't set up yet.") % {"combo": combo.title})
        slot_selection = []
        for entry in raw:
            option = next((o for o in options if str(o.dish_id) == str(entry.get("dish"))), None)
            if option is None:
                raise OrderError(_("That isn't an option for the %(slot)s of the %(combo)s.") % {"slot": slot.title.lower(), "combo": combo.title})
            if not option.dish.available:
                raise OrderError(_("%(dish)s is sold out. Pick another %(slot)s for the %(combo)s.") % {
                    "dish": option.dish.title, "slot": slot.title.lower(), "combo": combo.title})
            picked = price_dish(kits[option.dish_id], entry.get("cust"))
            picked.slot = slot.title
            picked.upcharge = option.price if combo.pricing == combo.FIXED else ZERO
            picks.append(picked)
            chosen.append(option)
            slot_selection.append({"dish": option.dish_id, **({"cust": picked.selection} if picked.selection else {})})
        if slot_selection:
            selection[str(slot.id)] = slot_selection
    unit = combo.unit_price(chosen) + sum((p.extra for p in picks), ZERO)
    combo_extras = Picked(dish=None)
    extras = list(combo.extras.all())
    _condiments_choice(line.get("cust") or {}, extras, combo_extras, combo.title)
    unit += combo_extras.extra
    mods.extend(combo_extras.mods)
    if combo_extras.selection:
        selection["extras"] = combo_extras.selection
    if picks and combo_extras.use:
        picks[0].use.update(combo_extras.use)  # combo extras are packed with the first dish
    return PricedLine("combo", combo, quantity, unit, picks, mods=mods, selection=selection, extra_picks=[combo_extras],
                      note=_clean_note(line.get("note")), raw_discount=line.get("discount"))


def _charge_condiments(priced):
    """Price condiments per order (see the module docstring): free ones first, then the price each."""
    free_left = {}
    for line in priced:
        for component_id, (condiment, per_unit) in line.condiments.items():
            if condiment.unlimited or condiment.price <= 0 or not per_unit:
                continue
            left = free_left.setdefault(component_id, condiment.free_per_order)
            free_each = min(per_unit, -(-left // line.quantity))  # round up: the customer gets the odd one free
            free_left[component_id] = max(0, left - free_each * line.quantity)
            charged = per_unit - free_each
            if charged:
                line.unit_price += condiment.price * charged
                line.selection.setdefault("charged", {})[str(component_id)] = charged


# ---------- discounts ----------

def _discount_problem(discount: Discount, *, channel, now, subtotal, item_count, scope, item=None):
    """Why `discount` can't be used here, or None if it can."""
    local = timezone.localtime(now)
    if not discount.active:
        return _("%(name)s isn't active.") % {"name": discount.name}
    if channel == "store" and not discount.on_register:
        return _("%(name)s can't be used on the register.") % {"name": discount.name}
    if channel != "store" and not (discount.online and discount.code):
        return _("%(name)s can't be used online.") % {"name": discount.name}
    if discount.starts_on and local.date() < discount.starts_on:
        return _("%(name)s starts on %(date)s.") % {"name": discount.name, "date": discount.starts_on}
    if discount.ends_on and local.date() > discount.ends_on:
        return _("%(name)s has ended.") % {"name": discount.name}
    if discount.weekdays and str(local.isoweekday()) not in discount.weekdays:
        return _("%(name)s isn't available today.") % {"name": discount.name}
    if discount.start_time and local.time() < discount.start_time or discount.end_time and local.time() >= discount.end_time:
        return _("%(name)s isn't available at this time.") % {"name": discount.name}
    if discount.min_subtotal is not None and subtotal < discount.min_subtotal:
        return _("%(name)s needs an order of at least $%(amount)s.") % {"name": discount.name, "amount": discount.min_subtotal}
    if discount.min_items and item_count < discount.min_items:
        return _("%(name)s needs at least %(count)s items.") % {"name": discount.name, "count": discount.min_items}
    if discount.max_uses is not None and discount.times_used >= discount.max_uses:
        return _("%(name)s has been used up.") % {"name": discount.name}
    if scope == Discount.ITEM and discount.scope == Discount.ORDER:
        return _("%(name)s is for the whole order.") % {"name": discount.name}
    if scope == Discount.ORDER and discount.scope == Discount.ITEM:
        return _("%(name)s is for one item.") % {"name": discount.name}
    if item is not None:
        dish_ids = {d.id for d in discount.dishes.all()}
        combo_ids = {c.id for c in discount.combos.all()}
        if dish_ids or combo_ids:
            allowed = item.id in (dish_ids if isinstance(item, Dish) else combo_ids)
            if not allowed:
                return _("%(name)s doesn't apply to %(item)s.") % {"name": discount.name, "item": item.title}
    return None


def _apply(discount_spec, base, *, channel, now, subtotal, item_count, scope, item, can_manual):
    """Turn a discount spec into an AppliedDiscount on `base`. Raises OrderError if it can't be used."""
    if not isinstance(discount_spec, dict):
        raise OrderError(_("The cart is malformed."))
    if "manual" in discount_spec:
        if not can_manual:
            raise OrderError(_("Only a manager can type in a discount."))
        manual = discount_spec["manual"]
        if not isinstance(manual, dict) or manual.get("kind") not in (Discount.PERCENT, Discount.AMOUNT):
            raise OrderError(_("The cart is malformed."))
        value = to_money(manual.get("value"))
        if value <= 0:
            raise OrderError(_("A discount must be more than zero."))
        if manual["kind"] == Discount.PERCENT and value > 100:
            raise OrderError(_("A discount can't be more than 100%."))
        label, kind, discount = _("Manual"), manual["kind"], None
    else:
        if "code" in discount_spec:
            code = str(discount_spec.get("code") or "").strip().upper()
            discount = Discount.objects.prefetch_related("dishes", "combos").filter(code=code).first() if code else None
            if discount is None:
                raise OrderError(_("That code isn't valid."))
        elif "offer" in discount_spec:
            discount = Discount.objects.prefetch_related("dishes", "combos").filter(
                pk=discount_spec.get("offer") if str(discount_spec.get("offer")).isdigit() else None, code__isnull=True).first()
            if discount is None:
                raise OrderError(_("That offer no longer exists."))
        else:
            raise OrderError(_("The cart is malformed."))
        problem = _discount_problem(discount, channel=channel, now=now, subtotal=subtotal,
                                    item_count=item_count, scope=scope, item=item)
        if problem:
            raise OrderError(problem)
        label, kind, value = discount.code or discount.name, discount.kind, discount.value
    if kind == Discount.PERCENT:
        amount = money(base * value / 100)
    else:
        amount = min(value, base)
    return AppliedDiscount(label=label, kind=kind, value=value, amount=max(amount, ZERO), discount=discount)


# ---------- the whole cart ----------

@dataclass
class PricedCart:
    lines: list
    subtotal: Decimal
    order_discount: AppliedDiscount = None
    order_discount_error: str = ""

    @property
    def line_discounts(self) -> Decimal:
        return sum((line.discount.amount for line in self.lines if line.discount), ZERO)

    @property
    def discount(self) -> Decimal:
        return self.line_discounts + (self.order_discount.amount if self.order_discount else ZERO)

    @property
    def tax(self) -> Decimal:
        return money((self.subtotal - self.discount) * tax_rate())

    @property
    def total(self) -> Decimal:
        return self.subtotal - self.discount + self.tax

    @property
    def item_count(self) -> int:
        return sum(line.quantity for line in self.lines)

    def applied_discounts(self):
        """[(PricedLine or None, AppliedDiscount)] in the order they apply."""
        found = [(line, line.discount) for line in self.lines if line.discount]
        if self.order_discount:
            found.append((None, self.order_discount))
        return found

    def as_json(self):
        """The quote the register shows. Money is in cents."""
        return {
            "lines": [{
                "unit": to_cents(line.unit_price), "gross": to_cents(line.gross),
                "discount": line.discount.as_json() if line.discount else None,
                "discountError": line.discount_error,
            } for line in self.lines],
            "orderDiscount": self.order_discount.as_json() if self.order_discount else None,
            "orderDiscountError": self.order_discount_error,
            "subtotal": to_cents(self.subtotal), "discount": to_cents(self.discount),
            "tax": to_cents(self.tax), "taxRate": str(tax_rate()), "total": to_cents(self.total),
            "count": self.item_count,
        }


def price_cart(lines, *, order_discount=None, channel="store", can_manual=False, strict=True, now=None) -> PricedCart:
    """
    Validate and price a cart. Raises OrderError for anything wrong with the lines.

    strict=False (the register's live quote) drops discounts that can't be used and says why in
    `discount_error` / `order_discount_error`, and lets combo slots that must be asked for fall back to their
    standard pick.
    """
    if not isinstance(lines, list):
        raise OrderError(_("The cart is malformed."))
    now = now or timezone.now()
    kits, combos = _load(lines)
    priced = [_price_line(line, kits, combos) for line in lines]
    _charge_condiments(priced)
    subtotal = sum((line.gross for line in priced), ZERO)
    cart = PricedCart(lines=priced, subtotal=subtotal)
    context = {"channel": channel, "now": now, "subtotal": subtotal, "item_count": cart.item_count, "can_manual": can_manual}

    for line in priced:
        if line.raw_discount:
            try:
                line.discount = _apply(line.raw_discount, line.gross, scope=Discount.ITEM, item=line.item, **context)
            except OrderError as e:
                if strict:
                    raise
                line.discount_error = str(e)
    if order_discount:
        try:
            base = subtotal - cart.line_discounts
            cart.order_discount = _apply(order_discount, base, scope=Discount.ORDER, item=None, **context)
        except OrderError as e:
            if strict:
                raise
            cart.order_discount_error = str(e)
    return cart


def offers_for_register(now=None):
    """Offers (discounts without a code) to list on the register, with whether they apply right now."""
    now = now or timezone.now()
    offers = []
    for discount in Discount.objects.filter(active=True, on_register=True, code__isnull=True).prefetch_related("dishes", "combos"):
        if discount.max_uses is not None and discount.times_used >= discount.max_uses:
            continue
        local = timezone.localtime(now).date()
        if discount.ends_on and local > discount.ends_on or discount.starts_on and local < discount.starts_on:
            continue
        offers.append({
            "id": discount.id, "name": discount.name, "note": discount.note, "kind": discount.kind,
            "value": to_cents(discount.value) if discount.kind == Discount.AMOUNT else format(discount.value.normalize(), "f"),
            "scope": discount.scope,
        })
    return offers
