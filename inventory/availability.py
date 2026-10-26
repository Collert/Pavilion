"""
The Availability panel's data (inventory/availability_panel.html, availability.js): every dish on the active menu
(and in its combos) and every component, each with its mode (Off / Auto / Always, see pos_server.models.AVAILABILITY)
and how much is left, split into three tabs: dishes, components & add-ons, and condiments.
"""
import math

from django.db.models import Q
from django.utils.translation import gettext as _, ngettext

from pos_server.models import Component, Dish, Menu, Option, OptionGroup, Station

MODES = ("off", "auto", "always")
LOW = 5  # "Running low" at this many portions left


def _number(value):
    """Inventory as a short number: whole when it is, else one decimal."""
    value = float(value or 0)
    return int(value) if value == int(value) else round(value, 1)


def _dish_rows(menu):
    dishes = Dish.objects.all() if menu is None else Dish.objects.filter(
        Q(menu=menu) | Q(combo_options__slot__combo__menu=menu)).distinct()
    dishes = dishes.select_related("station").prefetch_related("dishcomponent_set__component").order_by("title")
    by_station = {}
    for dish in dishes:
        parts = list(dish.dishcomponent_set.all())
        counted = [dc for dc in parts if dc.component.availability == "auto" and dc.component.crafting_option != "auto" and dc.quantity]
        can_make = min((math.floor((dc.component.inventory or 0) / dc.quantity) for dc in counted), default=None)
        names = [dc.component.title for dc in parts]
        by_station.setdefault(dish.station_id, []).append({
            "key": f"dish:{dish.id}", "kind": "dish", "id": dish.id, "name": dish.title, "mode": dish.availability,
            "stock": can_make, "unit": _("can make") if can_make is not None else _("not counted"),
            "low": can_make is not None and can_make <= LOW,
            "blocked_by": [dc.component.title for dc in parts if dc.component.availability == "off"],
            "sub": _("Uses %(parts)s") % {"parts": ", ".join(names)} if names else
                   _("%(station)s station") % {"station": dish.station.name},
        })
    return [{"name": station.name, "items": by_station[station.id]}
            for station in Station.objects.filter(archived=False) if station.id in by_station]


def _component_rows():
    components = Component.objects.filter(archived=False).prefetch_related("dishcomponent_set", "extra_uses").order_by("title")
    swapped_in = set(Option.objects.exclude(component=None).values_list("component_id", flat=True))
    addon_prices = {}
    for option in Option.objects.filter(group__pick=OptionGroup.ANY).exclude(component=None):
        addon_prices[option.component_id] = min(addon_prices.get(option.component_id, option.price), option.price)
    unit = {"ea": _("on hand"), "g": "g", "ml": "mL"}
    tabs = {"parts": {}, "condiments": {}}
    for component in components:
        parts = list(component.dishcomponent_set.all())
        extras = list(component.extra_uses.all())
        addon = component.id in addon_prices
        condiment = not parts and not addon and component.id not in swapped_in and bool(extras)
        used_in = len({("dish", dc.dish_id) for dc in parts} | {("dish", e.dish_id) if e.dish_id else ("combo", e.combo_id) for e in extras})
        counted = component.crafting_option != "auto"
        # Stored in g / ml; a kilo or more reads better as kg / L
        shown_stock, shown_unit = component.inventory or 0, unit.get(component.unit_of_measurement, component.unit_of_measurement)
        if component.unit_of_measurement in ("g", "ml") and abs(shown_stock) >= 1000:
            shown_stock, shown_unit = shown_stock / 1000, "kg" if component.unit_of_measurement == "g" else "L"
        smallest = min((q for q in [dc.quantity for dc in parts] + [e.quantity for e in extras] if q), default=1)
        if addon:
            what = _("Add-on +$%(price)s") % {"price": f"{addon_prices[component.id]:.2f}"}
        else:
            what = _("On the side") if condiment else _("Component")
        tab = tabs["condiments" if condiment else "parts"]
        group = _("Drinks") if component.category == "drink" else _("Food")
        tab.setdefault(group, []).append({
            "key": f"component:{component.id}", "kind": "component", "id": component.id, "name": component.title,
            "mode": component.availability,
            "stock": _number(shown_stock) if counted else None,
            "unit": shown_unit if counted else _("made to order"),
            "low": counted and (component.inventory or 0) / smallest <= LOW,
            "blocked_by": [],
            "used_in": used_in,
            "sub": what + (" · " + ngettext("in %(n)s dish", "in %(n)s dishes", used_in) % {"n": used_in} if used_in else ""),
        })
    return {name: [{"name": group, "items": items} for group, items in sorted(tab.items())] for name, tab in tabs.items()}


def catalog():
    """Everything the panel shows, as {"tabs": {"dishes": [groups], "parts": [groups], "condiments": [groups]}}."""
    return {"tabs": {"dishes": _dish_rows(Menu.active()), **_component_rows()}}


def apply_changes(changes):
    """
    Set modes: changes = [{"kind": "dish"|"component", "id": 1, "mode": "off"|"auto"|"always"}, ...].
    Components first, so dishes they block are up to date. Raises ValueError on anything malformed.
    """
    if not isinstance(changes, list) or not changes:
        raise ValueError("Expected a list of changes")
    models = {"component": Component, "dish": Dish}
    for change in sorted(changes, key=lambda c: c.get("kind") != "component" if isinstance(c, dict) else True):
        if not isinstance(change, dict) or change.get("kind") not in models or change.get("mode") not in MODES:
            raise ValueError("Each change needs a kind, an id and a mode")
        obj = models[change["kind"]].objects.filter(pk=change.get("id")).first()
        if obj is None:
            raise ValueError("Unknown item")
        if obj.availability != change["mode"]:
            obj.availability = change["mode"]
            obj.save()
