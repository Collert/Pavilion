"""Tests for screens: widget definitions, layout validation, rendering, the order board and access."""
import datetime
import json
import re
from unittest import mock

import requests
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.template.loader import get_template
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pos_server.models import Order, OrderStation, Station

from . import registry
from .layout import layout_errors, render_screen
from .models import Screen
from .providers import public_name
from .templatetags.screens import tracks


def node(widget, children=None, **params):
    spec = {"widget": widget, "params": params}
    if children is not None:
        spec["children"] = children
    return spec


class WidgetDefinitionTests(TestCase):
    def test_every_widget_loads_and_its_files_exist(self):
        from django.contrib.staticfiles import finders
        widgets = registry.widgets()
        self.assertTrue({"rows", "columns", "order_list", "clock", "weather", "now_playing", "text_card", "image"} <= widgets.keys())
        for widget in widgets.values():
            get_template(widget.template)
            for path in widget.css + widget.js:
                self.assertTrue(finders.find(path), f"{widget.name}: missing static file {path}")
            widget.provider()  # importable
            for param in widget.params.values():  # defaults pass their own checks
                if param.default is not None:
                    self.assertEqual(layout_errors(node(widget.name, **{param.name: param.default})), [], widget.name)


class LayoutValidationTests(TestCase):
    def test_a_good_tree(self):
        tree = node("rows", [node("clock"), None, node("columns", [node("text_card", title="Hi")], count=1)],
                    count=3, weights=[1, "132px", 2])
        self.assertEqual(layout_errors(tree), [])
        self.assertEqual(layout_errors(None), [])

    def test_problems_are_reported_with_where_they_are(self):
        tree = node("columns", [node("clock", colour="red"), node("nope"), node("text_card", background="red")],
                    count=2, weights=["wide"])
        errors = "\n".join(layout_errors(tree))
        self.assertIn("weights item 1", errors)
        self.assertIn("3 children for 2 slots", errors)
        self.assertIn("slot 1 (clock): clock has no param 'colour'", errors)
        self.assertIn("unknown widget 'nope'", errors)
        self.assertIn("background should be a colour", errors)

    def test_leaves_take_no_children_and_types_are_checked(self):
        self.assertIn("doesn't hold other widgets", layout_errors(node("clock", [None]))[0])
        self.assertTrue(layout_errors(node("rows", count=99)))
        self.assertTrue(layout_errors(node("rows", count=True)))  # a bool isn't an int
        self.assertTrue(layout_errors(node("image", src="javascript:alert(1)")))
        self.assertTrue(layout_errors(node("image", src="//evil.example/x.png")))
        self.assertEqual(layout_errors(node("image", src="/static/x.png")), [])

    def test_the_model_refuses_a_bad_layout(self):
        screen = Screen(name="Bad", slug="bad", layout=node("nope"))
        with self.assertRaises(ValidationError):
            screen.full_clean()

    def test_tracks(self):
        self.assertEqual(tracks([1, "132px", "auto", "25%"], 5),
                         "minmax(0, 1fr) calc(var(--u) * 132) auto 25% minmax(0, 1fr)")


class WidgetKindTests(TestCase):
    """Widgets are visible (layout slots), pop-ups or audio channels; the editor lists each kind on its own tab."""

    def test_kinds_are_listed_apart(self):
        self.assertTrue({"featured_dish", "qr_code", "promo_slides", "menu_board"} <= registry.widgets().keys())
        self.assertFalse(registry.widgets().keys() & (registry.popups().keys() | registry.audio().keys()))
        self.assertEqual(registry.widgets()["menu_board"].params["menu"].source, "menus")
        self.assertTrue(registry.widgets()["clock"].icon)

    def test_a_popup_needs_a_trigger(self):
        from django.core.exceptions import ImproperlyConfigured
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_popup.json"
            path.write_text(json.dumps({"title": "Bad", "template": "x.html", "kind": "popup"}), encoding="utf-8")
            with self.assertRaises(ImproperlyConfigured):
                registry._load(path)
            path.write_text(json.dumps({"title": "Old", "template": "x.html", "kind": "widget"}), encoding="utf-8")
            self.assertEqual(registry._load(path).kind, "visible")  # the old name still works


class NewWidgetTests(TestCase):
    def render(self, layout):
        screen = Screen(name="Test", slug="test", layout=layout, public=True)  # not saved: rendering needs no row
        return render_screen(screen, RequestFactory().get("/")).html

    def test_featured_dish_qr_code_and_promo_slides(self):
        from decimal import Decimal
        from pos_server.models import Discount, Dish
        station = Station.objects.first()
        dish = Dish.objects.create(title="Smash burger", price=Decimal("14.50"), station=station, description="Two patties")
        html = self.render(node("featured_dish", dish=dish.id, badge="New"))
        self.assertIn("Smash burger", html)
        self.assertIn("Two patties", html)
        self.assertIn("New", html)
        html = self.render(node("qr_code", opens="link", link="https://example.com/review", caption="Leave a review"))
        self.assertIn("<svg", html)
        self.assertIn("example.com/review", html)
        Discount.objects.create(name="Happy hour", note="Weekdays 3 to 5", kind="pct", value=Decimal("15"))
        html = self.render(node("promo_slides", headline="Burger Monday"))
        self.assertIn("Burger Monday", html)
        self.assertIn("Happy hour", html)
        self.assertIn("15% off", html)


class RenderTests(TestCase):
    def render(self, layout, **fields):
        screen = Screen.objects.create(name="Test", slug="test", layout=layout, public=True, **fields)
        return render_screen(screen, RequestFactory().get("/")), screen

    def test_nested_layout_renders_every_slot(self):
        rendered, _ = self.render(node("rows", [
            node("columns", [node("text_card", title="Left <b>"), None], count=2),
            node("text_card", title="Bottom"),
        ], count=2, weights=[3, "100px"]))
        html = rendered.html
        self.assertIn("grid-template-rows: minmax(0, 3fr) calc(var(--u) * 100)", html)
        self.assertIn("Left &lt;b&gt;", html)  # params are escaped
        self.assertIn("Bottom", html)
        self.assertEqual(html.count("slot-empty"), 1)
        self.assertEqual(rendered.css, ["screens/widgets/text_card.css", "screens/widgets/layout.css"])
        # Leaves are live regions, containers aren't
        self.assertEqual(len(re.findall(r'class="widget widget--text_card" id="[^"]+" data-region', html)), 2)
        self.assertNotRegex(html, r'widget--rows" id="[^"]+" data-region')

    def test_bad_saved_values_fall_back_instead_of_breaking(self):
        screen = Screen.objects.create(name="Old", slug="old", public=True)
        Screen.objects.filter(pk=screen.pk).update(layout=node("rows", [node("gone"), node("clock", hour12="yes")]))
        screen.refresh_from_db()
        html = render_screen(screen, RequestFactory().get("/")).html
        self.assertIn("Unknown widget", html)
        self.assertIn("data-clock", html)

    def test_a_failing_provider_shows_a_placeholder(self):
        with mock.patch("screens.providers.order_list", side_effect=RuntimeError("boom")), self.assertLogs("screens.layout", "ERROR"):
            rendered, _ = self.render(node("order_list"))
        self.assertIn("couldn&#x27;t load", rendered.html)

    def test_node_ids_change_with_the_layout(self):
        rendered, screen = self.render(node("clock"))
        screen.layout = node("clock", hour12=False)
        screen.save()
        self.assertNotEqual(rendered.html.split('id="')[1][:12],
                            render_screen(screen, RequestFactory().get("/")).html.split('id="')[1][:12])


class OrderBoardTests(TestCase):
    def setUp(self):
        self.kitchen = Station.objects.get(code="kitchen")
        self.bar = Station.objects.get(code="bar")
        self.now = timezone.now()

    def order(self, name, statuses, channel="store", picked_up=False, ready_ago=None, **fields):
        order = Order.objects.create(name=name, channel=channel, picked_up=picked_up, **fields)
        for station, status in zip((self.kitchen, self.bar), statuses):
            ready_at = self.now - datetime.timedelta(seconds=ready_ago) if status == OrderStation.STATUS_DONE and ready_ago is not None else None
            OrderStation.objects.create(order=order, station=station, status=status, ready_at=ready_at)
        return order

    def page(self, slug="order-board"):
        response = self.client.get(reverse("screen", kwargs={"slug": slug}))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_seeded_board_sorts_orders_into_preparing_and_ready(self):
        cooking = self.order("Priya Singh", [OrderStation.STATUS_APPROVED, OrderStation.STATUS_DONE])
        pending = self.order("Marcus", [OrderStation.STATUS_PENDING], channel="web")
        ready_old = self.order("Olivia Rose", [OrderStation.STATUS_DONE, OrderStation.STATUS_DONE], ready_ago=300)
        ready_new = self.order("Jun", [OrderStation.STATUS_DONE], ready_ago=2)
        self.order("Collected", [OrderStation.STATUS_DONE], picked_up=True, ready_ago=600)
        self.order("Courier", [OrderStation.STATUS_DONE], channel="delivery", ready_ago=5)
        self.order("Later", [OrderStation.STATUS_APPROVED], start_time=self.now + datetime.timedelta(hours=1))

        html = self.page()
        preparing, ready = html.split("orders--ready")[0], html.split("orders--ready")[1]
        self.assertIn("Priya S.", preparing)
        self.assertIn("Marcus", preparing)
        self.assertNotIn("Singh", html)  # only an initial on a public board
        self.assertLess(ready.index("Jun"), ready.index("Olivia R."))  # newest ready first
        for absent in ("Collected", "Courier", "Later"):
            self.assertNotIn(absent, html)
        self.assertIn(f"#{cooking.id:04d}", preparing)
        self.assertIn(f"#{pending.id:04d}", preparing)
        self.assertIn(f"#{ready_old.id:04d}", ready)
        # Only the one ready 2 s ago is fresh, and it asks for a refetch when the highlight should end
        self.assertEqual(ready.count("is-fresh"), 1)
        fresh_tile = ready[ready.index("is-fresh"):]
        self.assertLess(fresh_tile.index("Jun"), fresh_tile.index("Olivia"))
        self.assertIn("data-ends=", fresh_tile)
        # Live updates: the page listens for order changes and screen edits
        topics = json.loads(re.search(r'id="screen-topics" type="application/json">(.*?)</script>', html).group(1))
        self.assertEqual(set(topics), {"screens", "orders"})

    def test_overflow_is_counted(self):
        Screen.objects.create(name="Small", slug="small", public=True, layout=node("order_list", limit=2, more_text="more"))
        for n in range(5):
            self.order(f"Guest {n}", [OrderStation.STATUS_APPROVED])
        html = self.page("small")
        self.assertEqual(html.count("order-tile"), 2)
        self.assertIn("+ 3 more", html)
        self.assertIn('<span class="orders-count">5</span>', html)

    def test_public_name(self):
        self.assertEqual(public_name("  priya  van singh "), "priya S.")
        self.assertEqual(public_name("Jun"), "Jun")
        self.assertIsNone(public_name(""))
        self.assertIsNone(public_name(None))


class AccessTests(TestCase):
    def test_public_and_staff_only_screens(self):
        Screen.objects.create(name="Back office", slug="back", layout=node("clock"))
        self.assertEqual(self.client.get(reverse("screen", kwargs={"slug": "order-board"})).status_code, 200)
        response = self.client.get(reverse("screen", kwargs={"slug": "back"}))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login_view"), response.url)
        customer = User.objects.create_user("customer", password="x")
        self.client.force_login(customer)
        self.assertEqual(self.client.get(reverse("screen", kwargs={"slug": "back"})).status_code, 302)
        self.assertEqual(self.client.get(reverse("screens")).status_code, 302)
        staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("screen", kwargs={"slug": "back"})).status_code, 200)
        index = self.client.get(reverse("screens"))
        self.assertContains(index, "Back office")
        self.assertContains(index, "order_list")  # the widget catalogue
        self.assertEqual(self.client.get(reverse("screen", kwargs={"slug": "missing"})).status_code, 404)

    def test_editing_a_screen_nudges_open_screens(self):
        with mock.patch("pavilion.realtime.get_broker") as broker, self.captureOnCommitCallbacks(execute=True):
            Screen.objects.create(name="New", slug="new")
        self.assertEqual(broker.return_value.publish.call_args[0][0], "screens")

    def test_admin_shows_the_widget_reference_and_rejects_bad_layouts(self):
        admin = User.objects.create_superuser("admin", password="x")
        self.client.force_login(admin)
        screen = Screen.objects.get(slug="order-board")
        url = reverse("admin:screens_screen_change", args=[screen.pk])
        self.assertContains(self.client.get(url), "now_playing")
        response = self.client.post(url, {"name": "Board", "slug": "order-board", "public": "on", "width": 1920,
                                          "height": 1080, "background": "#F4F1EA", "padding": 36,
                                          "layout": json.dumps(node("nope"))})
        self.assertContains(response, "unknown widget")


@override_settings(WEATHER_API_KEY="test-key")
class WeatherTests(TestCase):
    def setUp(self):
        cache.clear()
        Screen.objects.create(name="Weather", slug="weather", public=True, layout=node("weather", location="Vancouver", units="c"))

    def test_fetched_on_the_server_and_cached(self):
        reply = mock.Mock(status_code=200)
        reply.json.return_value = {"current": {"temp_c": 12.6, "temp_f": 54.7, "is_day": 1,
                                               "condition": {"text": "Light rain", "code": 1183}}}
        with mock.patch("screens.providers.requests.get", return_value=reply) as get:
            first = self.client.get("/en/screens/weather").content.decode()
            self.client.get("/en/screens/weather")
        self.assertEqual(get.call_count, 1)
        self.assertIn("13°", first)
        self.assertIn("Light rain", first)
        self.assertIn("weather-drop", first)
        self.assertNotIn("test-key", first)

    def test_a_failed_fetch_shows_a_dash(self):
        with mock.patch("screens.providers.requests.get", side_effect=requests.ConnectionError), \
                self.assertLogs("screens.providers", "WARNING"):
            html = self.client.get("/en/screens/weather").content.decode()
        self.assertIn("—", html)


class MenuBoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal

        from pos_server.models import Dish, Menu
        cls.kitchen = Station.objects.get(code="kitchen")
        cls.bar = Station.objects.get(code="bar")
        cls.burger = Dish.objects.create(title="Burger", price=Decimal("14.50"), station=cls.kitchen, description="Two patties")
        cls.tea = Dish.objects.create(title="Tea", price=Decimal("2.00"), station=cls.bar, availability="off")
        cls.coffees = [Dish.objects.create(title=f"Coffee {n}", price=Decimal("3.00"), station=cls.bar) for n in range(7)]
        cls.menu = Menu.objects.create(title="Lunch", manual_on=True)
        cls.menu.dishes.set([cls.burger, cls.tea, *cls.coffees])

    def test_menu_templates_load_and_their_files_exist(self):
        from django.contrib.staticfiles import finders

        from .layout import check_value
        templates = registry.menu_templates()
        self.assertIn("menu_showcase", templates)
        self.assertNotIn("menu_showcase", registry.widgets())  # not placed on screens directly
        for template in templates.values():
            get_template(template.template)
            for path in template.css:
                self.assertTrue(finders.find(path), f"{template.name}: missing static file {path}")
            self.assertIn(template.layout["orientation"], ("portrait", "landscape"))
            for param in template.params.values():
                self.assertIsNone(check_value(param, param.default), f"{template.name}.{param.name}")

    def test_clean_fills_in_and_drops_what_doesnt_fit(self):
        from .menu_boards import clean
        display = clean({"pages": [{"slots": ["s:999", "combos", "s:%d" % self.kitchen.id, "extra"], "featured": "d:1"}],
                         "page_sec": 1, "group_fx": "spin", "look": {"accent": "red"}}, self.menu)
        self.assertEqual(display["template"], "menu_showcase")
        self.assertEqual(display["pages"][0]["slots"], ["", "", f"s:{self.kitchen.id}"])  # unknown groups go, 3 slots
        self.assertEqual((display["page_sec"], display["group_fx"], display["look"]["accent"]), (3, "slide", "#E9A64B"))
        self.assertEqual(clean({}, self.menu)["pages"], [{"featured": "", "slots": [f"s:{self.kitchen.id}", f"s:{self.bar.id}", ""]}])

    def test_board_splits_slots_into_screens_and_hides_sold_out(self):
        from .menu_boards import board
        display = {"pages": [{"featured": f"d:{self.burger.id}", "slots": [f"s:{self.bar.id}", "", ""]}], "sold": "show"}
        slot = board(self.menu, display)["pages"][0]["slots"][0]
        self.assertEqual((slot["count"], len(slot["screens"]), len(slot["screens"][0])), (8, 2, 6))
        self.assertTrue(any(item["out"] for item in slot["screens"][0]))
        slot = board(self.menu, {**display, "sold": "hide"})["pages"][0]["slots"][0]
        self.assertEqual(slot["count"], 7)

    def test_a_screen_shows_its_menu(self):
        self.menu.display = {"template": "menu_showcase", "look": {"badge": "Open till 8", "loved_count": 0},
                             "pages": [{"featured": f"d:{self.burger.id}", "slots": [f"s:{self.bar.id}", f"s:{self.kitchen.id}", ""]}]}
        self.menu.save()
        screen = Screen.objects.create(name="Board", slug="board", public=True, width=1080, height=1920, padding=0,
                                       layout={"widget": "menu_board", "params": {"menu": self.menu.id}})
        html = self.client.get(reverse("screen", args=["board"])).content.decode()
        self.assertIn("Open till 8", html)
        self.assertIn("Two patties", html)  # the featured dish
        self.assertIn("Coffee 6", html)
        self.assertIn('data-page-sec="20"', html)
        screen.layout = {"widget": "menu_board", "params": {"menu": 0}}
        screen.save()
        self.assertIn("Pick a menu for this board", self.client.get(reverse("screen", args=["board"])).content.decode())


class PopupTests(TestCase):
    """Pop-ups (screens/popups.py): moments from the cache, scheduled ones from the schedule, each its own region."""

    def setUp(self):
        cache.clear()
        self.screen = Screen(name="Board", slug="board", layout={"widget": "clock"}, popups=[
            {"widget": "order_ready", "params": {"seconds": 6}}, {"widget": "announcement", "params": {"style": "full"}},
            {"widget": "event_reminder", "params": {"before": 30, "seconds": 10}}])

    def render(self):
        return render_screen(self.screen, RequestFactory().get("/"))

    def test_moments_show_for_their_seconds(self):
        from .popups import record
        rendered = self.render()
        self.assertIn('id="pop-0-order_ready" data-region', rendered.html)
        self.assertNotIn("popup-card", rendered.html)
        self.assertIn("popups", rendered.topics)
        record("order_ready", {"name": "Priya S.", "number": "0142"})
        html = self.render().html
        self.assertIn("Priya S.", html)
        self.assertIn("popup--full", html)
        self.assertRegex(html, r'data-ends="\d+"')  # the page looks again when it should hide
        later = timezone.now() + datetime.timedelta(seconds=7)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertNotIn("Priya S.", self.render().html)

    def test_ready_orders_and_register_announcements(self):
        from types import SimpleNamespace
        from pos_server.signals import order_ready
        order_ready.send(sender=Order, order=SimpleNamespace(channel="web", name="Iryna Koval", id=57))
        self.assertIn("Iryna K.", self.render().html)
        self.assertIn("Order #0057", self.render().html)
        staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.client.force_login(staff)
        response = self.client.post(reverse("screens_announce"), json.dumps({"text": "Buns are out"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Buns are out", self.render().html)
        self.assertEqual(self.client.post(reverse("screens_announce"), "{}", content_type="application/json").status_code, 400)

    def test_event_reminder_shows_before_the_event(self):
        from events.models import Event
        soon = timezone.localtime() + datetime.timedelta(minutes=29, seconds=55)
        Event.objects.create(title="Trivia night", kind="open", start_date=soon.date(), end_date=soon.date(),
                             opens=soon.hour * 60 + soon.minute, closes=min(soon.hour * 60 + soon.minute + 60, 1680))
        start = timezone.make_aware(datetime.datetime.combine(soon.date(), datetime.time(soon.hour, soon.minute)))
        with mock.patch("django.utils.timezone.now", return_value=start - datetime.timedelta(minutes=30, seconds=-2)):
            self.assertIn("Trivia night", self.render().html)
        with mock.patch("django.utils.timezone.now", return_value=start - datetime.timedelta(minutes=40)):
            html = self.render().html
            self.assertNotIn("Trivia night", html)
            self.assertRegex(html, r'id="pop-2-event_reminder" data-region><span hidden data-ends="\d+">')  # wakes up for it
