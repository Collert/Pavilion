"""
The admin rail (design: AdminRail). Groups open when one of their pages is current. Pages whose url name is None
aren't built yet and stay hidden; a group with no built pages is hidden too.
"""
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

# (group id, label, icon path, [(page id, label, url name)])
MENU = [
    ("dashboard", _("Dashboard"), "M4 13h6V4H4z M14 20h6v-9h-6z M14 4h6v4h-6z M4 20h6v-3H4z", [
        ("home", _("Home"), "admin_home"),
        ("reports", _("Reports"), "admin_reports"),
    ]),
    ("orders", _("Orders"), "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6 M9 16h3", [
        ("retail", _("Retail orders"), "admin_orders"),
        ("catering", _("Catering"), "admin_catering"),
    ]),
    ("menu", _("Menu"), "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z M5 17a3 3 0 0 1 3-3h11 M9 8h6", [
        ("menus", _("Menus"), "admin_menus"),
        ("dishes", _("Dishes"), "admin_dishes"),
        ("combos", _("Combos"), "admin_combos"),
        ("recipes", _("Recipes"), "admin_recipes"),
        ("services", _("Services"), "admin_services"),
    ]),
    ("inventory", _("Inventory"), "M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z M3 7.5l9 4.5 9-4.5 M12 12v9", [
        ("components", _("Components"), "admin_components"),
        ("ingredients", _("Ingredients"), "admin_ingredients"),
        ("condiments", _("Condiments"), "admin_condiments"),
        ("disposables", _("Disposables"), "admin_disposables"),
        ("restocks", _("Restocks"), "admin_restocks"),
    ]),
    ("store", _("Store"), "M4 9h16l-1-4H5z M5 9v11h14V9 M9 20v-6h6v6", [
        ("branding", _("Branding"), "admin_branding"),
        ("hours", _("Store hours"), "admin_hours"),
        ("stations", _("Stations"), "admin_stations"),
        ("events", _("Events"), None),
        ("screens", _("Screens"), None),
        ("audio", _("Audio channels"), None),
        ("library", _("Library"), None),
    ]),
    ("team", _("Team"), "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z M2.5 20a6.5 6.5 0 0 1 13 0 M16 4.3a3.5 3.5 0 0 1 0 6.4 M18 14.2a6.5 6.5 0 0 1 3.5 5.8", [
        ("staff", _("Staff"), None),
        ("shifts", _("Shifts"), None),
        ("requests", _("Availability requests"), None),
        ("roles", _("Roles"), None),
        ("groups", _("Groups"), None),
        ("staffcards", _("Staff cards"), None),
    ]),
    ("customers", _("Customers"), "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z", [
        ("customers", _("Customers"), None),
        ("coupons", _("Coupons"), None),
        ("discounts", _("Discounts"), None),
        ("giftcards", _("Gift cards"), None),
        ("giftdesigns", _("Gift card designs"), None),
    ]),
]


def badges() -> dict:
    """{page id: count} shown as a pill on the rail: new catering orders."""
    from catering.models import CateringOrder
    return {"catering": CateringOrder.objects.filter(status=CateringOrder.NEW).count()}


def rail(active: str) -> list[dict]:
    counts = badges()
    groups = []
    for group_id, label, path, pages in MENU:
        items = [{"id": page_id, "label": page_label, "url": reverse(url_name), "current": page_id == active,
                  "badge": counts.get(page_id, 0)}
                 for page_id, page_label, url_name in pages if url_name]
        if not items:
            continue
        groups.append({"id": group_id, "label": label, "path": path, "url": items[0]["url"], "items": items,
                       "open": any(item["current"] for item in items),
                       "badge": sum(item["badge"] for item in items)})
    return groups
