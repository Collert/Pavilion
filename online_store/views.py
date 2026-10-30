from django.shortcuts import render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from events.hours import Hours
from pos_server.models import Dish
from pos_server.pricing import combo_queryset
from pos_server.views import group_by_station

from . import guest_orders, storefront, themes


def store_links(request) -> dict:
    """Where the theme's header, footer and buttons go. Pages not built yet are "" and their links are left out."""
    signed_in = request.user.is_authenticated
    return {"home": storefront.link("store_home"), "menu": storefront.link("menu"), "catering": storefront.link("store_catering"),
            "services": storefront.link("store_services"), "cart": storefront.link("store_cart") or storefront.link("menu"),
            "giftcards": storefront.link("store_gift_cards"), "install": storefront.link("store_install"),
            "account": storefront.link("account") if signed_in else "", "login": storefront.link("store_signin")}


def theme_page(request, page, context=None, active=""):
    """Render one of the live theme's pages (themes/<name>/<page>) with what every theme page gets."""
    cur = themes.current(request)
    links = store_links(request)
    if not storefront.gift_designs():
        links["giftcards"] = ""  # nothing on sale online: no link to an empty page
    nav = [(key, label, links[key]) for key, label in (("home", _("Home")), ("menu", _("Menu")), ("giftcards", _("Gift cards")),
                                                        ("catering", _("Catering")), ("services", _("Events & services"))) if links[key]]
    user = request.user
    hours = Hours()
    return render(request, cur.theme.template(page), {
        "hours": storefront.week_hours(hours=hours), "footer_hours": storefront.hours_summary(storefront.regular_hours(hours)),
        "theme": cur.theme, "t": cur.values, "store": cur.store, "logo_url": cur.logo_url, "preview": cur.preview,
        "css_vars": mark_safe(themes.css_vars(cur.theme, cur.values)), "app_theme_color": cur.values.get(cur.theme.app.get("theme", "paper"), ""), "status": storefront.open_now(hours=hours), "event_banner": storefront.event_banner(hours=hours),
        "cart_count": storefront.cart_count(request), "live_order": storefront.live_order(user, guest_orders.ids(request)), "links": links,
        "nav": nav, "active": active, "me": {"initials": storefront.initials(user), "first": user.first_name or user.username,
                                               "photo": storefront.photo(user)} if user.is_authenticated else None,
        "banner_url": links.get(cur.values.get("banner_link", ""), ""),
        "year": timezone.localdate().year,
        **(context or {}),
    })


def menu_sections(menu):
    """[(Station, [Dish or Combo])] for a menu: dishes by station, each combo with its first dish's station."""
    if menu is None:
        return []
    dishes = Dish.objects.filter(menu=menu).select_related("station").order_by("station__position", "id")
    sections = dict(group_by_station(dishes))
    for combo in combo_queryset().filter(menu=menu, active=True, archived=False):
        station = combo.section_station()
        if station is not None:
            sections.setdefault(station, []).append(combo)
    return sorted(sections.items(), key=lambda pair: (pair[0].position, pair[0].id))


def index(request):
    """The store's home page, as the live theme draws it (online_store/storefront.py works out what's on it)."""
    t = themes.current(request).values
    headline = t.get("headline", "")
    first, amp, rest = headline.partition(" & ")
    return theme_page(request, "home.html", {
        "headline": {"first": first, "amp": bool(amp), "rest": rest},
        "menus_now": storefront.menus_now() if t.get("show_menus") else [],
        "favourites": storefront.best_sellers(pictures=t.get("pictures", "photos")) if t.get("show_favourites") else [],
        "gifts": storefront.gift_teaser() if t.get("show_gifts", True) else None,
        "catering": storefront.catering_teaser() if t.get("show_catering") else None,
        "services": storefront.services_teaser() if t.get("show_services") else [],
        "pickup_minutes": storefront.pickup_minutes(),
        "hero_url": themes.media_url(t.get("hero")), "hero_drawing": _hero_drawing(),
        "side_dish": storefront.featured_dish(t.get("side_dish"), t.get("pictures", "photos")),
        "corner_dish": storefront.featured_dish(t.get("corner_dish"), t.get("pictures", "photos")),
    }, active="home")


def _hero_drawing():
    """The best seller's drawing, for a hero without a photo."""
    top = storefront.best_sellers(count=1)
    return storefront.drawing(top[0]["dish"].title) if top else "burger"
