"""
Menu boards: a menu shown on a screen with one of the menu templates (screens/widgets/menu_*.json, kind
"menu_template").

A menu's board is set in its Display tab (admin) and kept on `Menu.display`:

    {"template": "menu_showcase",                 # a menu template's name
     "look": {"accent": "#E9A64B", ...},          # the template's params (colours, fonts, texts)
     "pages": [{"featured": "d:12",               # a featured dish ("d:<id>") or combo ("c:<id>"), or ""
                "slots": ["s:3", "combos", ""]}], # one group per slot: a station's dishes and one-station combos ("s:<id>"), the other combos, or empty
     "sections": [{"id": 1, "name": "Breakfast",  # the menu's own sections ("m:<id>" in slots): picked dishes and
                   "items": ["d:12", "c:3"]}],    #   combos in this order, also listed with their station
     "sold": "show",                              # sold-out items: "show" (marked) or "hide"
     "page_fx": "fade", "page_sec": 20,           # pages take turns (cut, fade, slide, rise) every page_sec
     "group_fx": "slide", "group_sec": 8}         # a group with more items than its slot fits turns over every group_sec

`clean()` makes any stored or posted value safe to render; `board()` is what a template draws. The "menu_board"
screen widget (screens/providers.py) renders a picked menu with its template. `menu` can be anything with `dishes`
and `combos` querysets (the admin passes its unsaved draft).
"""
import datetime
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from pos_server.pricing import badge_for, badges

from . import registry
from .layout import clean_params

FX = ("cut", "fade", "slide", "rise")
DEFAULT_TEMPLATE = "menu_showcase"
MIN_SEC, MAX_SEC = 3, 600


def templates():
    return registry.menu_templates()


def template_of(display):
    found = templates()
    return found.get(display.get("template")) or found.get(DEFAULT_TEMPLATE) or next(iter(found.values()), None)


def _combos(menu):
    from pos_server.pricing import combo_queryset
    return combo_queryset().filter(pk__in=menu.combos.filter(archived=False, active=True).values("pk")).order_by("id")


def _combo_group(combo):
    """A combo whose choices are all made at one station is listed with that station's dishes, the rest under Combos."""
    own = combo.own_station()
    return f"s:{own.id}" if own else "combos"


SECTION_COLOR = "#5B6A62"
MAX_SECTIONS, MAX_SECTION_ITEMS = 20, 60


def item_keys(menu):
    """Every dish ("d:<id>") and combo ("c:<id>") key on the menu."""
    return ({f"d:{i}" for i in menu.dishes.filter(archived=False).values_list("id", flat=True)}
            | {f"c:{i}" for i in menu.combos.filter(archived=False, active=True).values_list("id", flat=True)})


def clean_sections(raw, menu=None):
    """The menu's own sections ([{"id", "name", "items"}]): items it doesn't have are dropped when `menu` is given."""
    known = item_keys(menu) if menu is not None else None
    out, ids = [], set()
    for section in (raw if isinstance(raw, list) else [])[:MAX_SECTIONS]:
        if not isinstance(section, dict):
            continue
        try:
            sid = int(section.get("id"))
        except (TypeError, ValueError):
            continue
        if sid <= 0 or sid in ids:
            continue
        ids.add(sid)
        items = [k for k in dict.fromkeys(section.get("items") or []) if isinstance(k, str) and (known is None or k in known)]
        out.append({"id": sid, "name": str(section.get("name") or "").strip()[:60], "items": items[:MAX_SECTION_ITEMS]})
    return out


def _sections_of(menu, sections):
    if sections is None:
        sections = clean_sections((menu.display or {}).get("sections") if isinstance(menu.display, dict) else None, menu)
    return sections


def groups(menu, sections=None):
    """[(key, name, color)] the menu can fill slots with: its stations (in station order, named by their menu
    heading), its combos that mix stations and its own sections ("m:<id>", from the Display tab)."""
    from pos_server.models import Station
    keys = {f"s:{sid}" for sid in menu.dishes.filter(archived=False).values_list("station_id", flat=True)}
    keys |= {_combo_group(combo) for combo in _combos(menu)}
    out = [(f"s:{s.id}", s.heading, s.color) for s in Station.objects.filter(archived=False) if f"s:{s.id}" in keys]
    if "combos" in keys:
        out.append(("combos", _("Combos"), "#14261F"))
    out += [(f"m:{s['id']}", s["name"] or _("Untitled section"), SECTION_COLOR) for s in _sections_of(menu, sections)]
    return out


def _sec(value, default):
    try:
        return min(max(int(value), MIN_SEC), MAX_SEC)
    except (TypeError, ValueError):
        return default


def clean(raw, menu=None):
    """A display value with every key present and valid (unknown groups and items are dropped when `menu` is given)."""
    raw = raw if isinstance(raw, dict) else {}
    template = template_of(raw)
    name = template.name if template else ""
    slots_per_page = template.layout["groups"] if template else 3
    sections = clean_sections(raw.get("sections"), menu)
    known = {key for key, _name, _color in groups(menu, sections)} if menu else None
    pages = []
    for page in (raw.get("pages") if isinstance(raw.get("pages"), list) else [])[:12]:
        page = page if isinstance(page, dict) else {}
        slots = [s if isinstance(s, str) and (known is None or s in known) else "" for s in (page.get("slots") or [])][:slots_per_page]
        slots += [""] * (slots_per_page - len(slots))
        featured = page.get("featured") if isinstance(page.get("featured"), str) else ""
        if not template or template.layout["featured"] == "none":
            featured = ""
        pages.append({"featured": featured, "slots": slots})
    if not pages:
        keys = [key for key, _name, _color in groups(menu, sections)] if menu else []
        pages = [{"featured": "", "slots": (keys + [""] * slots_per_page)[:slots_per_page]}]
    look = clean_params(template, raw.get("look")) if template else {}
    return {"template": name, "look": look, "pages": pages, "sections": sections,
            "sold": "hide" if raw.get("sold") == "hide" else "show",
            "page_fx": raw.get("page_fx") if raw.get("page_fx") in FX else "fade", "page_sec": _sec(raw.get("page_sec"), 20),
            "group_fx": raw.get("group_fx") if raw.get("group_fx") in FX else "slide", "group_sec": _sec(raw.get("group_sec"), 8)}


# ---------- what a board shows ----------

def _money(value):
    return f"${Decimal(value).quantize(Decimal('0.01'))}"


def _veg(dish):
    from pos_server.dish_info import dish_diets
    return any(d["key"] == "vegetarian" for d in dish_diets(dish))


def _dish(dish, deals=None, veg=None):
    return {"key": f"d:{dish.id}", "title": dish.title, "description": dish.description, "price": _money(dish.price),
            "out": not dish.available, "image": dish.image.url if dish.image else "", "letter": dish.title[:1],
            "deal": badge_for(deals or {}, dish), "veg": _veg(dish) if veg is None else veg}


def _combo(combo, deals=None):
    return {"key": f"c:{combo.id}", "title": combo.title, "description": combo.description,
            "price": _money(combo.standard_price()), "out": not combo.available,
            "image": combo.image.url if combo.image else "", "letter": combo.title[:1], "deal": badge_for(deals or {}, combo),
            "veg": False}


def items(menu, sections=None):
    """{group key: [item]} for every group of the menu, and {item key: item} for every item. A dish or combo also in
    one of the menu's own sections is listed there too, in the section's order."""
    from pos_server.dish_info import DISH_PREFETCH
    by_group, by_key = {}, {}
    deals = badges("store")  # automatic discounts running now (the admin's Discounts page)
    dishes = menu.dishes.filter(archived=False).select_related("station").prefetch_related(*DISH_PREFETCH)
    for dish in dishes.order_by("station__position", "id"):
        item = _dish(dish, deals)
        by_group.setdefault(f"s:{dish.station_id}", []).append(item)
        by_key[item["key"]] = item
    for combo in _combos(menu):
        item = _combo(combo, deals)
        by_group.setdefault(_combo_group(combo), []).append(item)
        by_key[item["key"]] = item
    for section in _sections_of(menu, sections):
        by_group[f"m:{section['id']}"] = [by_key[k] for k in section["items"] if k in by_key]
    return by_group, by_key


def best_sellers(menu, count, days=30):
    """The menu's most sold dishes over the last `days` (on their own or in combos)."""
    if count <= 0:
        return []
    from pos_server.models import Dish, OrderItem
    since = timezone.now() - datetime.timedelta(days=days)
    top = (OrderItem.objects.filter(dish__in=menu.dishes.filter(archived=False), order__timestamp__gte=since)
           .values("dish").annotate(n=Sum("quantity")).order_by("-n")[:count])
    from pos_server.dish_info import DISH_PREFETCH
    dishes = Dish.objects.prefetch_related(*DISH_PREFETCH).in_bulk([row["dish"] for row in top])
    deals = badges("store")
    return [_dish(dishes[row["dish"]], deals) for row in top if row["dish"] in dishes]


def screens_of(count, per):
    return max(1, -(-count // per))


def per_slot(template):
    """How many items each slot of the template shows at once (the first slot is often the big one)."""
    return template.layout["per_slot"] if template else [6, 6, 6]


def board(menu, display=None, store_name=""):
    """
    Everything a menu template draws: the look, and each page with its featured item and slots, each slot's items
    split into the screens it turns over through. Items on a page are numbered (`n`, across its slots), each slot
    has the first picture among its items (`image`), and `out` lists the page's sold-out items.
    """
    display = clean(display if display is not None else menu.display, menu)
    template = template_of(display)
    pers = per_slot(template)
    by_group, by_key = items(menu, display["sections"])
    names = {key: name for key, name, _color in groups(menu, display["sections"])}
    pages = []
    for page in display["pages"]:
        slots, out, n = [], [], 0
        for k, key in enumerate(page["slots"]):
            if not key:
                slots.append(None)
                continue
            per = pers[k] if k < len(pers) else pers[-1]
            shown = []
            for item in by_group.get(key, []):
                if display["sold"] == "show" or not item["out"]:
                    n += 1
                    shown.append({**item, "n": n})
                    if item["out"]:
                        out.append(item)
            slots.append({"key": key, "name": names.get(key, ""), "count": len(shown),
                          "image": next((i["image"] for i in shown if i["image"]), ""),
                          "screens": [shown[s:s + per] for s in range(0, len(shown), per)] or [[]]})
        featured = by_key.get(page["featured"])
        if featured and featured["out"] and display["sold"] == "hide":
            featured = None
        pages.append({"featured": featured, "slots": slots, "out": out})
    look = display["look"]
    return {
        "menu": menu, "display": display, "template": template, "look": look, "pages": pages,
        "title": look.get("title") or store_name or menu.title,
        "loved": best_sellers(menu, look.get("loved_count", 0)) if "loved_count" in look else [],
        "fonts": _fonts_url(template, look),
    }


FONT_FAMILIES = {
    "DM Serif Display": "DM+Serif+Display:ital@0;1", "Fraunces": "Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;1,9..144,400",
    "Playfair Display": "Playfair+Display:ital,wght@0,500;0,700;1,500", "Manrope": "Manrope:wght@500;600;700;800",
    "Inter": "Inter:wght@500;600;700;800", "Libre Franklin": "Libre+Franklin:wght@400;500;600;700",
    "Sora": "Sora:wght@400;500;600;700;800", "Archivo": "Archivo:wght@500;600;700;800",
    "Caveat Brush": "Caveat+Brush", "Kalam": "Kalam:wght@300;400;700", "Archivo Black": "Archivo+Black",
    "Schibsted Grotesk": "Schibsted+Grotesk:wght@400;500;700;800;900", "IBM Plex Mono": "IBM+Plex+Mono:wght@400;500;600",
    "Oswald": "Oswald:wght@400;500;600", "Rubik": "Rubik:wght@400;500;700;800;900",
    "JetBrains Mono": "JetBrains+Mono:wght@500;700;800", "Yellowtail": "Yellowtail",
    "Barlow Condensed": "Barlow+Condensed:wght@500;600;700", "Bebas Neue": "Bebas+Neue", "Permanent Marker": "Permanent+Marker",
    "Space Mono": "Space+Mono:wght@400;700", "Pacifico": "Pacifico", "Anton": "Anton",
}


def _fonts_url(template, look):
    """One Google Fonts stylesheet for the fonts the look picks."""
    families = [FONT_FAMILIES[f] for f in dict.fromkeys(v for k, v in look.items() if k.endswith("_font")) if f in FONT_FAMILIES]
    if not families:
        return template.layout.get("fonts", "") if template else ""
    return "https://fonts.googleapis.com/css2?" + "&".join(f"family={f}" for f in families) + "&display=swap"


def problems(display, menu):
    """Timing problems the Display tab reports: a group can't take longer to turn over than its page."""
    out = []
    pers = per_slot(template_of(display))
    by_group, _by_key = items(menu, display["sections"])
    overflow = any(screens_of(len([i for i in by_group.get(k, []) if display["sold"] == "show" or not i["out"]]), pers[n]) > 1
                   for p in display["pages"] for n, k in enumerate(p["slots"]) if k and n < len(pers))
    if len(display["pages"]) > 1 and overflow and display["group_sec"] > display["page_sec"]:
        out.append("timing")
    return out
