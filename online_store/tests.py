"""Tests for the online store: checkout (server-side pricing, payment checks, opening hours), themes, account, services."""
import datetime
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from deliveries.models import Delivery
from events.models import Event, StoreHours
from gift_cards.models import GiftCard
from payments.models import PaymentAuthorization, Transaction
from pos_server.models import Order, OrderStation
from pos_server.tests import MenuFixture


def at_noon():
    """Today at noon, for tests whose "open for the next few hours" would run past midnight late at night."""
    import contextlib
    import datetime as dt
    noon = timezone.make_aware(dt.datetime.combine(timezone.localdate(), dt.time(12, 0)))
    stack = contextlib.ExitStack()
    for target in ("django.utils.timezone.now", "pos_server.models.now"):
        stack.enter_context(mock.patch(target, return_value=noon))
    return stack


def open_hours(before=60, after=180, day=None, **cutoffs):
    """The store open today (an event) from `before` minutes ago to `after` from now (the week closed), cutoffs 0 unless given."""
    StoreHours.objects.update_or_create(pk=1, defaults={"week": [{"open": False, "r": []}] * 7, **{
        "pickup_after": 0, "pickup_before": 0, "delivery_after": 0, "delivery_before": 0, **cutoffs}})
    now = timezone.localtime()
    minute = now.hour * 60 + now.minute
    day = day or now.date()
    return Event.objects.update_or_create(title="Open", start_date=day, defaults={
        "kind": Event.OPEN, "end_date": day, "opens": max(0, minute - before), "closes": min(minute + after, 1680),
        "dine_in": True, "pickup": True, "delivery": True})[0]


class PlaceOrderTests(MenuFixture, TestCase):
    """Placing an order from checkout (pages._place): the card payment's transaction, gift cards, coupons, hours."""
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def setUp(self):
        self.today = open_hours(after=120)
        self.url = reverse("store_cart")
        self.client.post(reverse("menu"), {"dish": self.burger.id, "qty": "1", "op": "add", f"g-{self.temp_key}": self.medium.id,
                                           f"g-{self.bun_key}": self.gf.id})
        self.client.post(self.url, {"op": "tip:0"})

    def paid_transaction(self, amount):
        transaction = Transaction.objects.create(amount=Decimal(amount), successful=True)
        PaymentAuthorization.objects.create(payment_id=f"pay-{transaction.uuid}", amount=int(Decimal(amount) * 100),
                                            currency="CAD", transaction=transaction)
        return transaction

    def place(self, transaction=None, **extra):
        return self.client.post(self.url, {"name": "Iryna", "email": "iryna@example.com", "phone": "604-555-0123", "op": "place",
                                           "transaction_uuid": str(transaction.uuid) if transaction else "", **extra})

    def test_a_guest_sees_their_order_in_progress(self):
        self.place(self.paid_transaction("16.00"))
        order = Order.objects.get()
        live = self.client.get(reverse("store_home")).context["live_order"]
        self.assertEqual(live["order"], order)  # the header's "Cooking · #n" pill, from this browser's session
        self.assertContains(self.client.get(reverse("store_home")), reverse("order_status", args=[order.id]))
        self.assertIsNone(self.client_class().get(reverse("store_home")).context["live_order"])  # another browser
        Order.objects.filter(pk=order.pk).update(picked_up=True)
        self.assertIsNone(self.client.get(reverse("store_home")).context["live_order"])  # done: the pill goes

    def test_paid_order_is_created_pending(self):
        transaction = self.paid_transaction("16.00")
        response = self.place(transaction)
        order = Order.objects.get()
        self.assertRedirects(response, reverse("order_status", args=[order.id]) + "?placed=1", fetch_redirect_response=False)
        self.assertEqual((order.channel, order.total, order.phone), ("web", Decimal("16.00"), 6045550123))
        self.assertEqual(set(order.station_status_map().values()), {OrderStation.STATUS_PENDING})
        self.assertEqual(order.authorization.transaction, transaction)
        transaction.refresh_from_db()
        self.assertTrue(transaction.used)

    def test_unpaid_transaction_is_refused(self):
        self.place(Transaction.objects.create(amount=Decimal("16.00"), successful=False))
        self.assertFalse(Order.objects.exists())

    def test_underpaid_transaction_is_refused(self):
        transaction = self.paid_transaction("0.01")
        self.place(transaction)
        self.assertFalse(Order.objects.exists())
        transaction.refresh_from_db()
        self.assertFalse(transaction.used)

    def test_transaction_cannot_be_reused(self):
        transaction = self.paid_transaction("16.00")
        self.place(transaction)
        self.client.post(reverse("menu"), {"dish": self.burger.id, "qty": "1", "op": "add", f"g-{self.temp_key}": self.medium.id,
                                           f"g-{self.bun_key}": self.gf.id})
        self.place(transaction)
        self.assertEqual(Order.objects.count(), 1)

    def test_gift_card_can_cover_everything(self):
        card = GiftCard.objects.create(available_balance=Decimal("20.00"))
        self.client.post(self.url, {"op": "code", "code": card.number})
        self.place()
        card.refresh_from_db()
        self.assertEqual(card.available_balance, Decimal("4.00"))

    def test_a_signed_in_staff_members_cards_apply_at_checkout(self):
        from django.contrib.auth.models import User
        from gift_cards.models import StaffCard, StaffCardType
        marco = User.objects.create_user("marco", first_name="Marco", last_name="Bellini", email="marco@example.com", is_staff=True)
        meal = StaffCardType.objects.create(name="Shift meal", amount=Decimal("12"), channels=["store", "pickup"])
        off = StaffCardType.objects.create(name="Staff discount", kind="discount", discount_value=Decimal("25"), channels=["pickup"])
        balance = StaffCard.objects.create(type=meal, user=marco, number="1111222233334444", balance=Decimal("12"))
        StaffCard.objects.create(type=off, user=marco, number="5555666677778888")
        self.client.force_login(marco)  # the burger from setUp stays in the cart
        page = self.client.get(self.url).context
        self.assertEqual([label for label, _v, kind in page["lines"] if kind == "deal"], ["Staff discount", "Shift meal •• 4444"])
        self.assertEqual(page["to_pay"], Decimal("0.00"))  # 16.00 less 25% is 12.00, all on the meal card
        self.place()
        order = Order.objects.get()
        self.assertEqual((order.name, order.discount, order.total), ("Marco Bellini", Decimal("4.00"), Decimal("12.00")))
        balance.refresh_from_db()
        self.assertEqual(balance.balance, Decimal("0.00"))

    def test_coupon(self):
        from pos_server.models import Discount
        Discount.objects.create(name="Welcome", kind="pct", value=Decimal("10"), scope="order", code="WELCOME10", online=True)
        self.client.post(self.url, {"op": "code", "code": "welcome10"})
        self.place(self.paid_transaction("14.40"))
        order = Order.objects.get()
        self.assertEqual((order.subtotal, order.discount, order.total), (Decimal("16.00"), Decimal("1.60"), Decimal("14.40")))
        self.assertEqual(order.discounts.get().label, "WELCOME10")

    def test_menu_lists_combos(self):
        response = self.client.get(reverse("menu"))
        items = [it["obj"] for sec in response.context["sections"] for it in sec["items"]]
        self.assertIn(self.combo, items)
        sheet = self.client.get(reverse("menu") + f"?combo={self.combo.id}").context["sheet"]
        self.assertEqual([g["title"] for g in sheet["groups"]], ["Drink"])

    def test_closed_outside_store_hours(self):
        self.today.delete()
        self.place(self.paid_transaction("16.00"))
        self.assertFalse(Order.objects.exists())

    def test_delivery_stops_at_its_cutoff(self):
        with at_noon():
            self._delivery_stops_at_its_cutoff()

    def _delivery_stops_at_its_cutoff(self):
        open_hours(after=30, delivery_before=45)  # pickup is still on, delivery stopped
        address = {"address": "1 Main St", "city": "New Westminster", "postal": "V3M 1A1"}
        self.client.post(self.url, {"op": "method:delivery"})
        self.place(self.paid_transaction("16.00"), **address)
        self.assertFalse(Order.objects.exists())
        open_hours(after=180, delivery_before=45)
        priced = self.client.post(self.url, {"op": "preview", **address}).context["total"]
        self.place(self.paid_transaction(str(priced)), **address)
        self.assertEqual(Delivery.objects.get().order.channel, "delivery")

    def test_order_status_page(self):
        self.place(self.paid_transaction("16.00"))
        response = self.client.get(reverse("order_status", args=[Order.objects.get().id]))
        self.assertEqual(response.status_code, 200)

    def test_only_whoever_placed_an_order_sees_it(self):
        from online_store.models import RejectedOrder
        self.place(self.paid_transaction("16.00"))
        order = Order.objects.get()
        url = reverse("order_status", args=[order.id])
        self.client.cookies.pop("sessionid")  # a new session (signing out) doesn't forget a guest's orders: they're in a cookie
        self.assertEqual(self.client.get(url).status_code, 200)
        stranger = Client(HTTP_HOST="localhost")
        page = stranger.get(url)
        self.assertEqual(page.status_code, 403)
        self.assertContains(page, "isn’t yours to see", status_code=403)
        forged = Client(HTTP_HOST="localhost")
        forged.cookies["pavilion_orders"] = str(order.id)  # not signed
        self.assertEqual(forged.get(url).status_code, 403)
        # A customer's order is theirs on every device, and nobody else's
        customer = User.objects.create_user("ana", password="x")
        order.customer = customer
        order.save()
        self.assertEqual(self.client.get(url).status_code, 403)  # the browser that placed it, signed out
        stranger.force_login(customer)
        self.assertEqual(stranger.get(url).status_code, 200)
        RejectedOrder.objects.create(order_id=order.id + 1, reason="Out of buns", customer=customer)
        self.assertContains(stranger.get(reverse("order_status", args=[order.id + 1])), "Out of buns")
        self.assertEqual(self.client.get(reverse("order_status", args=[order.id + 1])).status_code, 403)


class ThemeTests(MenuFixture, TestCase):
    """online_store/themes.py and the home page the live theme draws."""
    @classmethod
    def setUpTestData(cls):
        from admin_portal.models import StoreSettings
        cls.build_menu()
        store = StoreSettings.load()
        store.name, store.address, store.city, store.phone = "Juniper Lane", "1200 Main St", "Vancouver", "604-555-0100"
        store.save()

    def test_every_theme_loads_with_its_templates_and_css(self):
        from django.contrib.staticfiles import finders
        from django.template.loader import get_template
        from online_store import themes
        found = themes.themes()
        self.assertIn("neighbourhood", found)
        for theme in found.values():
            for page in ("base.html", "home.html"):
                get_template(theme.template(page))
            for css in theme.css:
                self.assertTrue(finders.find(css), css)
            self.assertEqual(theme.clean({}), theme.defaults())

    def test_values_are_checked(self):
        from online_store import themes
        theme = themes.get("neighbourhood")
        values = theme.clean({"accent": "#1f4a3a", "paper": "red", "radius": 99, "outline": "2.5", "heading_font": "Comic Sans",
                              "hero": "../../etc/passwd", "banner": "yes"})
        self.assertEqual((values["accent"], values["paper"], values["radius"], values["outline"]), ("#1F4A3A", "#F6F0E4", 22, 2.5))
        self.assertEqual((values["heading_font"], values["hero"], values["banner"]), ("Fraunces", "", True))
        css = themes.css_vars(theme, values)
        self.assertIn("--accent: #1F4A3A;", css)
        self.assertIn("--outline: 2.5px;", css)
        self.assertIn("--heading-font: 'Fraunces', serif;", css)

    def test_home_shows_the_live_theme_and_the_draft_only_in_the_preview(self):
        from django.contrib.auth.models import User
        from admin_portal.models import StoreTheme
        from online_store import themes
        StoreTheme.objects.create(name="neighbourhood", live={"headline": "Live headline"}, draft={"headline": "Draft headline"})
        home = reverse("store_home")
        response = self.client.get(home)
        self.assertContains(response, "Live headline")
        self.assertContains(response, "1200 Main St")
        owner = User.objects.create_superuser("owner", password="x")
        self.client.force_login(owner)
        session = self.client.session
        session[themes.PREVIEW_KEY] = "neighbourhood"
        session.save()
        self.assertContains(self.client.get(home), "Live headline")  # a normal visit
        self.assertContains(self.client.get(home, headers={"Sec-Fetch-Dest": "iframe"}), "Draft headline")

    def test_hero_circles_show_the_picked_dishes_and_open_them(self):
        from admin_portal.models import StoreTheme
        from online_store import themes
        from pos_server.models import Dish
        theme = themes.get("neighbourhood")
        self.assertEqual(theme.clean({"side_dish": str(self.burger.id), "corner_dish": "-3"})["side_dish"], self.burger.id)
        self.assertEqual(theme.clean({"corner_dish": "-3"})["corner_dish"], 0)
        off_menu = Dish.objects.create(title="Secret fries", price=Decimal("4.00"), station=self.kitchen)
        StoreTheme.objects.create(name="neighbourhood", live={"side_dish": self.burger.id, "corner_dish": off_menu.id})
        response = self.client.get(reverse("store_home"))
        self.assertContains(response, f'href="{reverse("dish", args=[self.burger.id])}"')
        self.assertNotContains(response, reverse("dish", args=[off_menu.id]))  # not on a live menu: the drawing stays
        self.assertEqual(response.context["corner_dish"], None)

    def test_hours_and_open_status(self):
        from online_store import storefront
        now = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        week = [{"open": False, "r": [[420, 1260]]}] * 7
        week[now.weekday()] = {"open": True, "r": [[420, 1260]]}
        StoreHours.objects.update_or_create(pk=1, defaults={"week": week})
        status = storefront.open_now(now)
        self.assertEqual((status["open"], status["text"], status["short"]), (True, "Open now · till 9 PM today", "7–9"))
        self.assertFalse(storefront.open_now(now.replace(hour=22))["open"])
        week = storefront.week_hours(now.date())
        self.assertEqual([h["time"] for h in week if h["today"]], ["7 AM – 9 PM"])
        self.assertEqual(sum(1 for h in storefront.hours_summary(week) if h["time"] == "Closed"), len(storefront.hours_summary(week)) - 1)
        self.assertEqual(storefront.drawing("Smash burger"), "burger")
        self.assertEqual(storefront.drawing("Mystery"), "plate")


class StoreOrderingTests(MenuFixture, TestCase):
    """The themed menu, dish sheet, session cart, checkout and placed order (online_store/pages.py)."""
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def setUp(self):
        open_hours()

    def add_burger(self, **extra):
        data = {"dish": self.burger.id, "qty": "1", "op": "add", f"g-{self.temp_key}": self.medium.id,
                f"g-{self.bun_key}": self.gf.id, **extra}
        return self.client.post(reverse("menu"), data)

    def test_sheet_adds_merges_and_changes_lines(self):
        sheet = self.client.get(reverse("menu") + f"?dish={self.burger.id}").context["sheet"]
        self.assertEqual([g["title"] for g in sheet["groups"]][:3], ["Add-ons", "Bun", "Temperature"])
        self.assertEqual(sheet["missing"], [])  # required groups start on their first option
        self.assertTrue(next(g for g in sheet["groups"] if g["title"] == "Temperature")["opts"][0]["on"])
        preview = self.client.post(reverse("menu"), {"dish": self.burger.id, "qty": "2", f"g-{self.addons_key}": self.avocado_addon.id,
                                                     f"g-{self.temp_key}": self.medium.id, f"g-{self.bun_key}": self.gf.id}).context["sheet"]
        self.assertEqual((preview["unit"], preview["total"]), (Decimal("18.25"), Decimal("36.50")))  # 14.50 + 2.25 + 1.50
        self.assertRedirects(self.add_burger(), reverse("menu"), fetch_redirect_response=False)
        self.add_burger()
        cart = self.client.session["cart"]
        self.assertEqual([line["qty"] for line in cart["lines"]], [2])  # the same burger twice is one line
        rows = self.client.get(reverse("store_cart")).context["rows"]
        self.assertEqual((rows[0]["qty"], rows[0]["total"]), (2, Decimal("32.00")))
        key = rows[0]["key"]
        self.client.post(reverse("store_cart"), {"op": f"dec:{key}"})
        self.assertEqual(self.client.session["cart"]["lines"][0]["qty"], 1)
        self.client.post(reverse("store_cart"), {"op": f"remove:{key}"})
        self.assertTrue(self.client.get(reverse("store_cart")).context["empty"])

    def test_phone_tab_bar_and_cart_bar(self):
        """Phones get Home, Order, Gift cards and Profile along the bottom, and a bar to the cart above them while it
        has something in it (not on the checkout page, which is the cart)."""
        home = self.client.get(reverse("store_home"))
        self.assertContains(home, 'class="nb-dock"')
        self.assertContains(home, f'href="{reverse("menu")}"')
        self.assertNotContains(home, "nb-cartbar-btn")
        self.add_burger(qty="2")
        menu = self.client.get(reverse("menu"))
        self.assertEqual(menu.context["cart_count"], 2)  # items, not lines
        self.assertContains(menu, "nb-cartbar-btn")
        self.assertContains(menu, "$32.00")
        self.assertNotContains(self.client.get(reverse("store_cart")), "nb-cartbar")

    def test_gift_card_order_with_a_tip(self):
        card = GiftCard.objects.create(available_balance=Decimal("100"))
        self.add_burger()
        url = reverse("store_cart")
        self.client.post(url, {"op": "code", "code": card.number})
        self.client.post(url, {"op": "tip:10"})
        page = self.client.post(url, {"name": "Iryna", "email": "iryna@example.com", "op": "preview"}).context
        self.assertTrue(page["ready"])
        self.assertEqual((page["tip"], page["to_pay"]), (Decimal("1.60"), Decimal("0.00")))
        response = self.client.post(url, {"name": "Iryna", "email": "iryna@example.com", "op": "place"})
        order = Order.objects.get()
        self.assertRedirects(response, reverse("order_status", args=[order.id]) + "?placed=1", fetch_redirect_response=False)
        self.assertEqual((order.tip, order.total, order.name, order.email), (Decimal("1.60"), Decimal("17.60"), "Iryna", "iryna@example.com"))
        self.assertEqual(order.stations.count(), 1)
        self.assertEqual(self.client.session["cart"]["lines"], [])
        o = self.client.get(reverse("order_status", args=[order.id])).context["o"]
        self.assertEqual(o["totals"][-1], ("Tip", "$1.60"))

    def test_card_payment_opens_the_card_form(self):
        self.add_burger()
        response = self.client.post(reverse("store_cart"), {"name": "Iryna", "email": "iryna@example.com", "op": "place"})
        page = response.context
        self.assertEqual(page["pay"]["amount"], Decimal("18.40"))  # 16.00 + 15% tip
        self.assertIn("confirmation_needed=1", page["pay"]["url"])
        self.assertFalse(Order.objects.exists())
        # in the summary, in place of Place order: no overlay, a frame that sizes itself, the place button for checkout.js
        self.assertContains(response, "data-card-frame")
        self.assertContains(response, '<button hidden name="op" value="place"')
        self.assertNotContains(response, "nb-overlay")

    def test_first_scheduled_time_is_never_too_early(self):
        from unittest import mock
        from online_store.pages import _schedule
        with at_noon():
            now = timezone.now()
            open_hours(before=60, after=240)  # open around noon, whatever time the tests run
        at = now.replace(minute=now.minute // 5 * 5, second=50, microsecond=0)  # 20 min later is :x0:50, not a whole 5 minutes
        with mock.patch("django.utils.timezone.now", return_value=at), mock.patch("pos_server.models.now", return_value=at):
            when = _schedule({"when": "later", "date": timezone.localdate(at).isoformat(), "time": ""}, [], False)
        self.assertTrue(when["ok"], when["message"])

    def test_scheduling_follows_events_and_menus(self):
        self.add_burger()
        url = reverse("store_cart")
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        Event.objects.filter(start_date=timezone.localdate()).update(closes=1440)  # nothing past midnight
        self.client.post(url, {"op": "when:later"})
        when = self.client.post(url, {"op": f"date:{tomorrow.isoformat()}"}).context["when"]
        self.assertEqual((when["ok"], when["message"]), (False, "We’re closed that day."))
        Event.objects.create(title="Tomorrow", kind=Event.OPEN, start_date=tomorrow, end_date=tomorrow, opens=540, closes=1020)
        when = self.client.post(url, {"op": "preview", "hour": "11", "minute": "30", "ampm": "AM"}).context["when"]
        self.assertTrue(when["ok"], when["message"])
        self.assertIn("tomorrow at 11:30 AM", when["message"])
        when = self.client.post(url, {"op": "preview", "hour": "6", "minute": "00", "ampm": "PM"}).context["when"]
        self.assertFalse(when["ok"])
        self.menu.manual_on = False
        self.menu.save()
        when = self.client.post(url, {"op": "preview", "hour": "11", "minute": "30", "ampm": "AM"}).context["when"]
        self.assertEqual(when["message"], "Burger isn’t on the menu then.")

    def test_switching_menus_swaps_the_tabs_and_dishes(self):
        """The tabs are data-live links: live-page.js swaps only data-regions, so the tabs and dishes must be ones."""
        from pos_server.models import Dish, Menu
        brunch = Menu.objects.create(title="Brunch", manual_on=True, position=self.menu.position + 1)
        Dish.objects.create(title="Shakshuka", price=Decimal("14.00"), station=self.kitchen).menu.add(brunch)
        page = self.client.get(reverse("menu") + f"?actual={brunch.id}").content.decode()
        self.assertIn('id="nb-menu-tabs" data-region', page)
        self.assertIn('id="nb-menu-dishes" data-region', page)
        dishes = page.split('id="nb-menu-dishes"')[1]
        self.assertIn("Shakshuka", dishes)
        self.assertNotIn(">Burger<", dishes)

    def test_a_combos_picks_needn_t_be_on_the_menu_themselves(self):
        """A kids' main sold only inside the kids' meal: the combo is on the menu, its dish isn't (it was refused)."""
        from online_store.pages import _schedule
        from pos_server.models import Dish
        kids_burger = Dish.objects.create(title="Kids cheeseburger", price=Decimal("7.00"), station=self.kitchen)  # on no menu
        self.combo.menu.add(self.menu)
        with at_noon():
            open_hours(before=60, after=240)
            when = _schedule({"when": "now", "date": "", "time": ""}, [self.combo], False)
            self.assertTrue(when["ok"], when["message"])
            self.assertFalse(_schedule({"when": "now", "date": "", "time": ""}, [kids_burger], False)["ok"])


class AccountTests(MenuFixture, TestCase):
    """online_store/account.py: sign in and join, the account pages, saved cards at checkout."""
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User
        cls.build_menu()
        cls.user = User.objects.create_user("sofia@example.com", email="sofia@example.com", password="juniperlane", first_name="Sofia")

    def setUp(self):
        open_hours()

    def test_sign_in_join_and_sign_out(self):
        self.assertRedirects(self.client.get(reverse("account")), reverse("store_signin") + "?next=/store/account", fetch_redirect_response=False)
        self.assertTrue(self.client.post(reverse("store_signin"), {"email": "sofia@example.com", "password": "nope"}).context["form"]["failed"])
        self.assertRedirects(self.client.post(reverse("store_signin"), {"email": "SOFIA@example.com", "password": "juniperlane"}),
                             reverse("account"), fetch_redirect_response=False)
        self.client.get(reverse("store_signout"))
        page = self.client.post(reverse("store_join"), {"first": "Noor", "last": "Haddad", "email": "sofia@example.com", "password": "longenough"}).context
        self.assertIn("already an account", page["problem"])
        response = self.client.post(reverse("store_join"), {"first": "Noor", "last": "Haddad", "email": "noor@example.com",
                                                            "phone": "604-555-0171", "password": "longenough", "news": "1"})
        self.assertRedirects(response, reverse("account"), fetch_redirect_response=False)
        from django.contrib.auth.models import User
        noor = User.objects.get(email="noor@example.com")
        self.assertEqual((noor.customer_profile.phone, noor.customer_profile.newsletter), (6045550171, True))

    def test_avatar_photo_beats_gravatar_beats_initials(self):
        """storefront.photo: the photo put on the account, else the staff photo, else a Gravatar that's transparent
        when there's none (the initials show through), else nothing."""
        import hashlib
        import io
        import tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        from online_store import storefront
        from users.models import Customer, Staff
        digest = hashlib.sha256(b"sofia@example.com").hexdigest()
        self.user.email = " Sofia@Example.com "
        self.assertEqual(storefront.photo(self.user), f"https://gravatar.com/avatar/{digest}?s=96&d=blank&r=g")
        self.assertEqual(storefront.photo(User(username="noemail")), "")
        self.user.email = "sofia@example.com"
        picture = io.BytesIO()
        Image.new("RGB", (4, 4), "teal").save(picture, "PNG")
        self.client.force_login(self.user)
        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            self.assertContains(self.client.get(reverse("store_home")), f"gravatar.com/avatar/{digest}")
            bad = self.client.post(reverse("account_profile"), {"op": "save", "first": "Sofia", "email": "sofia@example.com",
                                                                "photo": SimpleUploadedFile("me.png", b"not a picture", content_type="image/png")}, follow=True)
            self.assertContains(bad, "isn’t a photo we can use")
            self.assertFalse(Customer.objects.get(user=self.user).profile_picture)
            self.client.post(reverse("account_profile"), {"op": "save", "first": "Sofia", "email": "sofia@example.com",
                                                          "photo": SimpleUploadedFile("me.png", picture.getvalue(), content_type="image/png")})
            prof = Customer.objects.get(user=self.user)
            self.assertEqual(prof.profile_picture.name, f"pfps/{self.user.id}.png")
            self.assertEqual(storefront.photo(User.objects.get(pk=self.user.pk)), prof.profile_picture.url)
            self.assertContains(self.client.get(reverse("account_profile")), prof.profile_picture.url)
            self.client.post(reverse("account_profile"), {"op": "photo_clear"})
            self.assertFalse(Customer.objects.get(user=self.user).profile_picture)
            staff = Staff.objects.create(user=self.user)
            staff.photo.save("sofia.png", SimpleUploadedFile("sofia.png", picture.getvalue()))
            self.assertEqual(storefront.photo(User.objects.get(pk=self.user.pk)), staff.photo.url)

    def test_profile_addresses_and_cards(self):
        from users.models import SavedCard
        self.client.force_login(self.user)
        url = reverse("account_profile")
        self.client.post(url, {"op": "save", "first": "Sofia", "last": "Moreau", "email": "sofia@example.com", "phone": "604 555 0110",
                               "org": "Lumen Studio", "order_texts": "1"})
        profile = self.user.customer_profile
        profile.refresh_from_db()
        self.assertEqual((profile.phone_text, profile.organization, profile.order_texts, profile.newsletter), ("604-555-0110", "Lumen Studio", True, False))
        self.client.post(url, {"op": "address_save", "label": "Work", "line": "355 Railway St", "unit": "200"})
        self.assertEqual(self.user.saved_addresses.get().one_line, "355 Railway St, 200")
        with self.settings(DEBUG=True):
            self.client.post(reverse("save-card"), json.dumps({"demo": True}), content_type="application/json")
            self.client.post(reverse("save-card"), json.dumps({"demo": True}), content_type="application/json")
        cards = list(self.user.saved_cards.all())
        self.assertEqual([c.is_default for c in cards], [True, False])
        self.client.post(url, {"op": f"card_remove:{cards[0].id}"})
        self.assertTrue(SavedCard.objects.get().is_default)

    def test_checkout_with_a_saved_card_then_reorder(self):
        from payments.models import PaymentAuthorization
        from users.models import SavedCard
        self.client.force_login(self.user)
        SavedCard.objects.create(user=self.user, square_customer_id="demo:c", square_card_id="demo:card", brand="VISA", last4="4242", is_default=True)
        self.client.post(reverse("menu"), {"dish": self.burger.id, "qty": "2", "op": "add", f"g-{self.temp_key}": self.well.id,
                                           f"g-{self.bun_key}": self.gf.id})
        url = reverse("store_cart")
        self.assertTrue(self.client.get(url).context["state"]["pay"].startswith("card:"))
        response = self.client.post(url, {"name": "Sofia", "op": "place"})
        order = Order.objects.get()
        self.assertRedirects(response, reverse("order_status", args=[order.id]) + "?placed=1", fetch_redirect_response=False)
        self.assertEqual(order.customer, self.user)
        self.assertEqual(PaymentAuthorization.objects.get().status, PaymentAuthorization.AUTHORIZED)
        self.assertEqual(order.payments.get(method="card").card_last4, "4242")
        page = self.client.get(reverse("account")).context
        self.assertEqual((page["rows"][0]["o"], page["d"]["number"]), (order, order.id))
        self.client.post(reverse("account_reorder", args=[order.id]))
        line = self.client.session["cart"]["lines"][0]
        self.assertEqual((line["id"], line["qty"], line["cust"]["options"][self.temp_key]), (self.burger.id, 2, self.well.id))
        self.assertEqual(self.client.get(reverse("account_receipt", args=[order.id])).status_code, 200)

    def add_burger(self):
        self.client.post(reverse("menu"), {"dish": self.burger.id, "qty": "1", "op": "add", f"g-{self.temp_key}": self.well.id,
                                           f"g-{self.bun_key}": self.gf.id})

    def test_an_email_with_an_account_offers_to_sign_in_and_the_cart_survives_it(self):
        from users.models import SavedAddress, SavedCard
        SavedCard.objects.create(user=self.user, square_customer_id="demo:c", square_card_id="demo:card", brand="VISA", last4="4242", is_default=True)
        SavedAddress.objects.create(user=self.user, label="Home", line="455 E 8th Ave", city="Vancouver", is_default=True)
        self.add_burger()
        url = reverse("store_cart")
        page = self.client.get(url).content.decode()
        self.assertLess(page.index('id="co-who"'), page.index('id="co-how"'))  # the details come before pickup or delivery
        self.assertFalse(self.client.post(url, {"email": "noor@example.com"}).context["has_account"])
        page = self.client.post(url, {"name": "Sofia", "email": "SOFIA@example.com", "op": "pay:new"})
        self.assertTrue(page.context["has_account"])
        signin = page.context["signin_url"]
        self.assertIn("email=SOFIA%40example.com", signin)
        self.assertEqual(self.client.get(signin).context["form"]["email"], "SOFIA@example.com")
        self.assertRedirects(self.client.post(signin, {"email": "sofia@example.com", "password": "juniperlane", "next": url}), url,
                             fetch_redirect_response=False)
        page = self.client.post(url, {"op": "method:delivery"}).context
        self.assertEqual(page["count"], 1)  # the cart came along
        self.assertFalse(page["has_account"])
        state = page["state"]
        self.assertEqual((state["name"], state["email"], state["address"]), ("Sofia", "sofia@example.com", "455 E 8th Ave"))
        self.assertEqual(state["pay"], f"card:{SavedCard.objects.get().id}")  # the guest's "new card" belonged to nobody

    def test_delivering_to_a_new_address_saves_it(self):
        from users.models import SavedAddress
        home = SavedAddress.objects.create(user=self.user, label="Home", line="455 E 8th Ave", city="Vancouver", is_default=True)
        self.client.force_login(self.user)
        self.add_burger()
        url = reverse("store_cart")
        page = self.client.post(url, {"op": "method:delivery"})
        self.assertEqual(page.context["state"]["address_id"], str(home.id))
        self.assertNotContains(page, 'name="address"')  # a saved address shows as its option, not as fields
        page = self.client.post(url, {"op": "address:new"})
        self.assertEqual((page.context["state"]["address_id"], page.context["state"]["address"]), ("new", ""))
        self.assertContains(page, 'name="address"')
        self.assertContains(page, 'value="save_address"')
        self.client.post(url, {"address": "355 Railway St", "unit": "200", "city": "Vancouver", "address_label": "Work",
                               "phone": "604 555 0110", "name": "Sofia"})
        self.client.post(url, {"op": "place", "transaction_uuid": self.paid_transaction()})
        self.assertEqual(Order.objects.get().delivery.get().destination, "355 Railway St, Vancouver")
        work = self.user.saved_addresses.get(label="Work")
        self.assertEqual((work.line, work.unit, work.is_default), ("355 Railway St", "200", False))

    def test_removing_your_own_gift_card_keeps_it_off(self):
        """A gift card sent to the customer's email is used by itself; Remove has to stick (it came straight back)."""
        card = GiftCard.objects.create(available_balance=Decimal("100"), email="sofia@example.com")
        self.client.force_login(self.user)
        self.add_burger()
        url = reverse("store_cart")
        self.assertEqual([c for c, _a in self.client.get(url).context["gifts"]], [card])
        self.client.post(url, {"op": f"drop_gift:{card.id}"})
        self.assertEqual(self.client.get(url).context["gifts"], [])
        self.client.post(url, {"op": "code", "code": card.number})  # typed in again: back on
        self.assertEqual([c for c, _a in self.client.get(url).context["gifts"]], [card])

    def test_a_new_address_isn_t_saved_with_the_switch_off(self):
        self.client.force_login(self.user)
        self.add_burger()
        url = reverse("store_cart")
        self.client.post(url, {"op": "method:delivery"})  # no saved addresses: the fields straight away
        self.client.post(url, {"op": "save_address"})
        self.client.post(url, {"address": "355 Railway St", "phone": "604 555 0110", "name": "Sofia"})
        self.client.post(url, {"op": "place", "transaction_uuid": self.paid_transaction()})
        self.assertTrue(Order.objects.exists())
        self.assertFalse(self.user.saved_addresses.exists())

    def test_eat_here_or_take_out(self):
        from events.models import Event
        self.client.force_login(self.user)
        self.add_burger()
        url = reverse("store_cart")
        self.assertEqual(self.client.get(url).context["state"]["eat"], "togo")
        self.assertEqual(self.client.post(url, {"op": "eat:here"}).context["state"]["eat"], "here")
        Event.objects.filter(title="Open").update(dine_in=False)  # the dining room is closed: take-out only
        page = self.client.get(url).context
        self.assertEqual((page["state"]["eat"], page["dine_open"]), ("togo", False))
        Event.objects.filter(title="Open").update(dine_in=True)
        self.client.post(url, {"op": "eat:here"})
        self.client.post(url, {"name": "Sofia", "op": "place", "transaction_uuid": self.paid_transaction()})
        self.assertFalse(Order.objects.get().to_go_order)

    def paid_transaction(self):
        from payments.models import PaymentAuthorization, Transaction
        priced = self.client.get(reverse("store_cart")).context
        transaction = Transaction.objects.create(amount=priced["to_pay"], successful=True)
        PaymentAuthorization.objects.create(payment_id="demo:p", amount=0, currency="CAD", transaction=transaction,
                                            status=PaymentAuthorization.AUTHORIZED)
        return str(transaction.uuid)

    def test_a_new_card_is_saved_in_square_while_paying(self):
        from payments.models import PaymentAuthorization, Transaction
        self.client.force_login(self.user)
        self.add_burger()
        url = reverse("store_cart")
        pay = self.client.post(url, {"name": "Sofia", "op": "place"}).context["pay"]
        self.assertIn("save_card=1", pay["url"])
        self.client.post(url, {"op": "save_card"})  # unticked
        self.assertNotIn("save_card", self.client.post(url, {"name": "Sofia", "op": "place"}).context["pay"]["url"])
        self.client.post(url, {"op": "save_card"})
        config = self.client.get(self.client.post(url, {"name": "Sofia", "op": "place"}).context["pay"]["url"]).context["config"]
        self.assertTrue(config["extra"]["save_card"])
        card = {"id": "ccof:1", "brand": "VISA", "last4": "1111", "exp_month": 4, "exp_year": 2030}
        with mock.patch("payments.square.create_customer", return_value="cust-1"), \
                mock.patch("payments.square.save_card", return_value=card) as saved, \
                mock.patch("payments.square.charge_card", return_value="pay-1") as charged:
            answer = self.client.post(reverse("process-web-payment"), json.dumps({"token": "cnon:ok", **config["extra"]}),
                                      content_type="application/json").json()
        self.assertEqual(answer["status"], "success")
        self.assertEqual(saved.call_args.args[:2], ("cnon:ok", "cust-1"))
        self.assertEqual(charged.call_args.args[:2], ("cust-1", "ccof:1"))
        self.assertFalse(charged.call_args.kwargs["autocomplete"])  # held until the kitchen accepts
        kept = self.user.saved_cards.get()
        self.assertEqual((kept.square_card_id, kept.last4, kept.exp_year, kept.is_default), ("ccof:1", "1111", 2030, True))
        self.assertEqual(User.objects.get(pk=self.user.pk).customer_profile.square_customer_id, "cust-1")
        transaction = Transaction.objects.get(uuid=config["extra"]["transaction_uuid"])
        self.assertTrue(transaction.successful)
        self.assertEqual(transaction.authorization.get().status, PaymentAuthorization.AUTHORIZED)
        self.client.post(url, {"name": "Sofia", "op": "place", "transaction_uuid": str(transaction.uuid)})
        self.assertEqual(Order.objects.get().payments.get(method="card").card_last4, "1111")

    def test_saving_the_same_card_again_replaces_it(self):
        from online_store.account import save_customer_card
        card = {"customer": "cust-1", "id": "ccof:1", "brand": "VISA", "last4": "1111", "exp_month": 4, "exp_year": 2030}
        save_customer_card(self.user, card)
        with mock.patch("payments.square.disable_card") as disabled:
            save_customer_card(self.user, {**card, "id": "ccof:2"})
        disabled.assert_called_once_with("ccof:1")
        self.assertEqual(list(self.user.saved_cards.values_list("square_card_id", flat=True)), ["ccof:2"])

    def test_a_made_up_square_customer_is_replaced_by_a_real_one(self):
        """seed_demo's customers have "demo:" Square ids; saving a real card under one failed (CUSTOMER_NOT_FOUND)."""
        from payments.views import square_customer
        from users.models import Customer
        Customer.objects.filter(user=self.user).update(square_customer_id="demo:cust-sofia")
        with mock.patch("payments.square.create_customer", return_value="cust-real") as created:
            self.assertEqual(square_customer(self.user), "cust-real")
            self.assertEqual(square_customer(self.user), "cust-real")  # kept: made once
        created.assert_called_once()
        self.assertEqual(Customer.objects.get(user=self.user).square_customer_id, "cust-real")


class ServicesPageTests(TestCase):
    def test_services_page_and_add_to_a_request(self):
        from services.models import Service
        room = Service.objects.create(title="Back room", category=Service.VENUE, pricing=Service.HOUR, price=Decimal("75"),
                                      minimum=2, catering=False, details={"seated": 24, "standing": 35})
        Service.objects.create(title="Event server", category=Service.STAFF, pricing=Service.HOUR, price=Decimal("38"))
        Service.objects.create(title="Hidden", category=Service.EXTRA, pricing=Service.UNIT, price=Decimal("1"), online=False)
        page = self.client.get(reverse("store_services")).context
        self.assertEqual([v["s"] for v in page["venues"]], [room])
        self.assertIn("Seats 24, or 35 standing", page["venues"][0]["facts"])
        self.assertEqual([[x["s"].title for x in g["items"]] for g in page["groups"]], [["Event server"]])
        self.assertRedirects(self.client.get(reverse("store_catering") + f"?add={room.id}"), reverse("store_catering"), fetch_redirect_response=False)
        quote = self.client.get(reverse("store_catering")).context
        self.assertEqual(quote["q"]["services"], {str(room.id): {"q": 1, "h": 2}})
        self.assertIn(room, [x["s"] for g in quote["svc_groups"] for x in g["items"]])



class SignUpVerificationTests(TestCase):
    def test_joining_sends_a_confirmation_link(self):
        from unittest import mock
        with mock.patch("users.accounts.send", return_value=True) as sent:
            self.client.post(reverse("store_join"), {"first": "Ada", "last": "Ng", "email": "ada@example.com", "password": "long-enough-1"})
        self.assertEqual((sent.call_args.args[0], sent.call_args.args[1]), ("verify_email", "ada@example.com"))


class GiftCardPageTests(TestCase):
    """Buying a gift card in the store (online_store/pages.py: gift_cards_page)."""
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User
        from gift_cards.models import GiftCardDesign
        from users.models import SavedCard
        GiftCardDesign.objects.all().delete()  # the starter design gift_cards 0005 makes
        cls.design = GiftCardDesign.objects.create(name="Thank you", amounts=[15, 25], message="Thanks for everything.", on_register=False)
        cls.user = User.objects.create_user("ana", email="ana@example.com", password="x", first_name="Ana")
        SavedCard.objects.create(user=cls.user, square_customer_id="demo:c", square_card_id="demo:card", brand="VISA", last4="4242", is_default=True)

    def test_buy_one_with_a_saved_card(self):
        from unittest import mock
        from gift_cards.models import GiftCard
        from payments.models import PaymentAuthorization
        url = reverse("store_gift_cards")
        page = self.client.get(url)
        self.assertEqual((page.context["st"]["amt"], page.context["amount"]), (25, 25))
        self.assertFalse(page.context["can_buy"])  # no email yet
        fields = {"op": "place", "to": "Sam", "email": "sam@example.com", "from": "Ana", "msg": "Thanks!", "other": ""}
        page = self.client.post(url, fields)
        self.assertIsNotNone(page.context["pay"])  # signed out: the card form
        self.client.force_login(self.user)
        with mock.patch("gift_cards.giftcards.send_email") as sent:
            page = self.client.post(url, fields)
        done = page.context["done"]
        self.assertEqual((done["card"].original, done["card"].email, done["sent"]), (Decimal("25"), "sam@example.com", True))
        self.assertEqual([c.args[:2] for c in sent.call_args_list],
                         [("gift_card_gifted", "sam@example.com"), ("gift_card_receipt", "ana@example.com")])  # and the buyer's receipt
        self.assertEqual(GiftCard.find(done["link"].rsplit("/", 1)[-1]), done["card"])
        self.assertEqual(done["order"].authorization.status, PaymentAuthorization.CAPTURED)

    def test_the_page_carries_every_design_and_only_buy_posts(self):
        from gift_cards.models import GiftCardDesign
        other = GiftCardDesign.objects.create(name="Birthday", amounts=[20, 40], custom=True, min_amount=10, max_amount=200, on_register=False)
        page = self.client.get(reverse("store_gift_cards"))
        self.assertEqual([d["name"] for d in page.context["designs_data"]], ["Thank you", "Birthday"])
        self.assertContains(page, 'id="gift-designs"')
        self.assertNotContains(page, "data-search")  # nothing posts while choosing
        self.client.force_login(self.user)
        from unittest import mock
        with mock.patch("gift_cards.giftcards.send_email"):
            page = self.client.post(reverse("store_gift_cards"), {"op": "place", "design": other.id, "amt": "other", "other": "75",
                                                                 "self": "1", "to": "", "email": ""})
        done = page.context["done"]
        self.assertEqual((done["card"].design, done["card"].original, done["card"].email), (other, Decimal("75"), "ana@example.com"))
        self.assertEqual(self.client.get(reverse("store_gift_cards")).context["me"]["first"], "Ana")  # the header's, not overridden

    def test_an_amount_the_design_doesnt_have_falls_back(self):
        page = self.client.post(reverse("store_gift_cards"), {"design": self.design.id, "amt": "999", "email": "sam@example.com"})
        self.assertEqual(page.context["st"]["amt"], 25)



class StaffAccountTests(TestCase):
    """The staff side of the account (online_store/work.py): Shifts, Up for grabs, Availability and time off."""

    def setUp(self):
        from team.models import Shift
        from users.models import Role, Staff
        self.cook = Role.objects.get_or_create(name="Cook")[0]
        self.aisha = User.objects.create_user("aisha", first_name="Aisha", last_name="Okafor", email="aisha@example.com", is_staff=True)
        Staff.objects.create(user=self.aisha).roles.add(self.cook)
        self.marco = User.objects.create_user("marco", first_name="Marco", email="marco@example.com", is_staff=True)
        Staff.objects.create(user=self.marco).roles.add(self.cook)
        self.day = timezone.localdate() + datetime.timedelta(days=3)
        self.mine = Shift.objects.create(user=self.aisha, role=self.cook, date=self.day, start=540, end=1020, break_minutes=30, published=True)
        self.client.force_login(self.aisha)

    def test_customers_dont_see_work(self):
        customer = User.objects.create_user("sofia", email="sofia@example.com")
        self.client.force_login(customer)
        self.assertRedirects(self.client.get(reverse("account_shifts")), reverse("account"), fetch_redirect_response=False)
        page = self.client.get(reverse("account_profile"))
        self.assertNotContains(page, "Up for grabs")
        self.assertContains(page, "Company")

    def test_profile_staff_section(self):
        page = self.client.get(reverse("account_profile"))
        self.assertContains(page, "Emergency contact")
        self.assertContains(page, "Up for grabs")
        self.assertNotContains(page, "shows on catering invoices")
        self.client.post(reverse("account_profile"), {"op": "save", "first": "Aisha", "last": "Okafor", "email": "aisha@example.com",
                                                      "phone": "604-555-0127", "ec_name": "Grace Okafor", "ec_rel": "Mother", "ec_phone": "604-555-0164"})
        self.aisha.staff_profile.refresh_from_db()
        self.assertEqual((self.aisha.staff_profile.emergency_name, self.aisha.staff_profile.emergency_relation, self.aisha.staff_profile.phone),
                         ("Grace Okafor", "Mother", "604-555-0127"))
        self.assertEqual(self.client.get(reverse("account_profile")).context["phone"], "604-555-0127")

    def test_shifts_give_away_and_take_back(self):
        from team.models import ShiftOffer
        url = reverse("account_shifts")
        page = self.client.get(url)
        self.assertEqual(page.context["next"]["time"], "9:00 AM – 5:00 PM")
        self.assertContains(page, "Give away")
        self.assertContains(self.client.get(url, {"give": self.mine.id}), "Give this shift away?")
        self.client.post(url, {"op": "give", "shift": self.mine.id, "note": "Family thing"})
        offer = ShiftOffer.objects.get()
        self.assertContains(self.client.get(url), "Take it back")
        self.client.post(url, {"op": "cancel", "offer": offer.id})
        offer.refresh_from_db()
        self.assertEqual(offer.status, ShiftOffer.CANCELLED)

    def test_swap_dialog_and_ask(self):
        from team.models import Shift, ShiftOffer
        theirs = Shift.objects.create(user=self.marco, role=self.cook, date=self.day + datetime.timedelta(days=1), start=540, end=1020, published=True)
        url = reverse("account_shifts")
        md = self.client.get(url, {"swap": self.mine.id, "who": self.marco.id}).context["modal"]
        self.assertEqual(([p["first"] for p in md["people"]], [t["id"] for t in md["theirs"]]), (["Marco"], [theirs.id]))
        with mock.patch("misc_tools.email.send"), self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {"op": "swap", "shift": self.mine.id, "who": self.marco.id, "theirs": theirs.id})
        offer = ShiftOffer.objects.get()
        self.assertEqual((offer.kind, offer.to, offer.theirs), (ShiftOffer.SWAP, self.marco, theirs))
        # Marco says yes on Up for grabs
        self.client.force_login(self.marco)
        market = self.client.get(reverse("account_market"))
        self.assertEqual(market.context["staff"]["badge"], 1)
        self.assertContains(market, "asked to swap shifts with you")
        self.client.post(reverse("account_market"), {"op": "accept", "offer": offer.id})
        offer.refresh_from_db()
        self.assertEqual(offer.status, ShiftOffer.ACCEPTED)

    def test_grab_from_market(self):
        from team.models import Shift
        open_shift = Shift.objects.create(role=self.cook, date=self.day + datetime.timedelta(days=2), start=600, end=900, published=True)
        page = self.client.get(reverse("account_market"))
        self.assertEqual(page.context["sections"][0]["items"][0]["s"], open_shift)
        self.client.post(reverse("account_market"), {"op": "grab", "shift": open_shift.id})
        open_shift.refresh_from_db()
        self.assertEqual(open_shift.user, self.aisha)

    def test_time_off_and_availability_requests(self):
        from team.models import Availability, TimeOff
        url = reverse("account_time")
        page = self.client.get(url)
        self.assertEqual(page.context["shift_days"][0]["date"], self.day.isoformat())
        day = self.day.isoformat()
        self.client.post(url, {"op": "timeoff", "rq": "off", "first": day, "last": day, "all_day": "1", "category": "Vacation"})
        off = TimeOff.objects.get()
        self.assertContains(self.client.get(url), "1 shift would open up")
        self.client.post(url, {"op": "withdraw", "key": f"t{off.id}"})
        self.assertFalse(TimeOff.objects.exists())
        post = {"op": "availability", "rq": "avail", "from": day}
        for d in range(7):
            post.update({f"d{d}": "1" if d != 3 else "0", f"d{d}_start": "09:00", f"d{d}_end": "17:00"})
        self.client.post(url, post)
        req = Availability.objects.get()
        self.assertEqual((req.windows["0"], req.windows["3"]), ([[540, 1020]], []))
        # a bad request keeps what they typed
        page = self.client.post(url, {**post, "op": "availability", "from": timezone.localdate().isoformat(), "reason": "Classes"})
        self.assertEqual((page.status_code, page.context["fa"]["note"]), (200, "Classes"))


class StoreThrottleTests(TestCase):
    """The store's sheet and checkout posts are capped per visitor (online_store/pages.py: too_fast)."""

    def test_checkout_posts_are_rate_limited(self):
        from django.core.cache import cache
        from django.test import override_settings

        from online_store import pages
        url = reverse("store_cart")
        self.client.post(url, {"op": "tip:0"})  # starts the visitor's session, which is what's counted
        cache.clear()
        with override_settings(THROTTLE=True, THROTTLE_REQUESTS=1000), mock.patch("pavilion.throttle.time.time", return_value=1000.0):
            codes = [self.client.post(url, {"op": "tip:0"}).status_code for _ in range(pages.STORE_LIMIT)]
            self.assertNotIn(429, codes)
            response = self.client.post(url, {"op": "tip:0"}, follow=True)
            self.assertContains(response, "a lot of taps")
            self.client.post(url, {"op": "place"})  # placing is never skipped (it fails here for an empty cart)


class StoreGiftCardsLinkTests(TestCase):
    """Gift cards in the store's header and on the home page, only while a design is sold online."""

    def test_header_and_home_section(self):
        from gift_cards.models import GiftCardDesign
        GiftCardDesign.objects.all().delete()
        home = self.client.get(reverse("store_home"))
        self.assertNotContains(home, "Buy a gift card")
        self.assertNotIn("giftcards", [key for key, _l, _u in home.context["nav"]])
        GiftCardDesign.objects.create(name="Classic", amounts=[25, 50], online=True)
        home = self.client.get(reverse("store_home"))
        keys = [key for key, _l, _u in home.context["nav"]]
        self.assertEqual(keys[keys.index("menu") + 1], "giftcards")  # between Menu and Catering
        self.assertContains(home, "Buy a gift card")
        self.assertEqual(home.context["gifts"]["from"], 25)



class ComboSheetTests(MenuFixture, TestCase):
    """The store's combo sheet (online_store/cart.py: combo_sheet): what each choice starts on."""

    @classmethod
    def setUpTestData(cls):
        from pos_server.models import ComboSlot, ComboSlotOption, Dish
        cls.build_menu()
        cookie = Dish.objects.create(title="Cookie", price=Decimal("3.50"), station=cls.bar)
        brownie = Dish.objects.create(title="Brownie", price=Decimal("4.25"), station=cls.bar)
        treat = ComboSlot.objects.create(combo=cls.combo, title="Treat", required=False, position=2)
        ComboSlotOption.objects.create(slot=treat, dish=brownie, price=Decimal("2.00"), is_default=True)
        ComboSlotOption.objects.create(slot=treat, dish=cookie, price=Decimal("2.00"))

    def picked(self, sheet, title):
        group = next(g for g in sheet["groups"] if g["title"] == title)
        return group, [o["label"] for o in group["opts"] if o["on"]]

    def test_a_sold_out_default_isnt_picked(self):
        from online_store import cart as store_cart
        from pos_server.models import Combo, Dish
        Dish.objects.filter(pk=self.coffee.pk).update(availability="off")
        sheet = store_cart.combo_sheet(Combo.objects.get(pk=self.combo.pk))
        _group, on = self.picked(sheet, "Drink")
        self.assertEqual(on, ["Tea"])  # coffee is the default, but it's sold out

    def test_an_optional_choice_starts_on_no_thanks(self):
        from online_store import cart as store_cart
        from pos_server.models import Combo
        sheet = store_cart.combo_sheet(Combo.objects.get(pk=self.combo.pk))
        group, on = self.picked(sheet, "Treat")
        self.assertEqual((group["req"], group["required"], on), ("Optional", False, ["No thanks"]))
        self.assertEqual(sheet["unit"], Decimal("16.00"))  # no treat, no upcharge


def browser(n=1):
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/phone-{n}", "keys": {"p256dh": f"key-{n}", "auth": f"auth-{n}"}}


def placed_here(client, order):
    """`client` placed `order` as a guest (the signed cookie online_store/guest_orders.py keeps)."""
    from django.http import HttpResponse
    from online_store import guest_orders
    response = HttpResponse()
    response.set_signed_cookie(guest_orders.COOKIE, str(order.id), salt=guest_orders.SALT)
    client.cookies[guest_orders.COOKIE] = response.cookies[guest_orders.COOKIE].value


class StoreAppTests(MenuFixture, TestCase):
    """The installable store app (online_store/app.py): manifest, icons, service worker, install page, push sign-ups."""
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def post(self, name, body, *args, client=None):
        return (client or self.client).post(reverse(name, args=args), json.dumps(body), content_type="application/json")

    def test_the_manifest_follows_the_store_and_its_theme(self):
        from admin_portal.models import StoreSettings, StoreTheme
        store = StoreSettings.load()
        store.name = "Corner Kitchen"
        store.save()
        StoreTheme.objects.update_or_create(name="neighbourhood", defaults={"live": {"paper": "#FFFFFF"}, "draft": {}})
        manifest = self.client.get(reverse("store_manifest")).json()
        self.assertEqual((manifest["name"], manifest["scope"], manifest["start_url"]), ("Corner Kitchen", "/store/", "/store/"))
        self.assertEqual((manifest["theme_color"], manifest["background_color"]), ("#FFFFFF", "#FFFFFF"))
        self.assertEqual([i["purpose"] for i in manifest["icons"]], ["any", "any", "maskable"])
        icon = self.client.get(manifest["icons"][0]["src"])
        self.assertEqual((icon.status_code, icon.content[:4]), (200, b"\x89PNG"))
        self.assertEqual(self.client.get(reverse("store_icon", args=[400])).status_code, 400)
        self.assertEqual(manifest["screenshots"], [])
        store.app_screenshots = [{"name": "store_app/narrow-0-1.png", "form": "narrow", "width": 412, "height": 915, "page": "menu"}]
        store.save()
        (shot,) = self.client.get(reverse("store_manifest")).json()["screenshots"]
        self.assertEqual((shot["form_factor"], shot["sizes"], shot["label"]), ("narrow", "412x915", "The menu"))

    def test_store_pages_carry_the_app(self):
        from push import vapid
        page = self.client.get(reverse("menu"))
        self.assertContains(page, reverse("store_manifest"))
        self.assertContains(page, 'id="store-push"')
        worker = self.client.get(reverse("store_worker"))
        self.assertContains(worker, vapid.public_key())
        self.assertEqual(worker["Service-Worker-Allowed"], "/store/")

    def test_the_install_page(self):
        page = self.client.get(reverse("store_install"))
        self.assertContains(page, "Apple’s way")
        self.assertContains(page, "data-install-button")
        self.assertContains(page, "What it doesn’t")
        self.assertContains(self.client.get(reverse("menu")), reverse("store_install"))  # the footer links it

    def guest_order(self):
        from pos_server.orders import create_order
        order = create_order(lines=[self.dish(self.burger)], channel="web", initial_status=OrderStation.STATUS_PENDING, name="Iryna")
        placed_here(self.client, order)
        return order

    def test_following_an_order_from_its_page(self):
        from push.models import PushSubscription
        order = self.guest_order()
        page = self.client.get(reverse("order_status", args=[order.id]))
        self.assertContains(page, "Get notified about this order")
        self.assertContains(page, "nb-notify-sheet")
        stranger = Client(HTTP_HOST="localhost")
        self.assertEqual(self.post("store_push_order", {"subscription": browser(2), "auto": False}, order.id, client=stranger).status_code, 403)
        answer = self.post("store_push_order", {"subscription": browser(), "auto": True}, order.id).json()
        self.assertTrue(answer["linked"])
        sub = PushSubscription.objects.get()
        self.assertEqual((sub.user, list(sub.orders.all())), (None, [order]))
        # Order updates turned off for this device: the page no longer links it by itself, but a tap does
        sub.order_updates = False
        sub.save()
        sub.orders.clear()
        self.assertFalse(self.post("store_push_order", {"subscription": browser(), "auto": True}, order.id).json()["linked"])
        self.assertTrue(self.post("store_push_order", {"subscription": browser(), "auto": False}, order.id).json()["linked"])
        sub.refresh_from_db()
        self.assertTrue(sub.order_updates)

    def test_a_guest_subscription_gets_its_account_and_can_be_forgotten(self):
        from push.models import PushSubscription
        self.post("store_push_sync", {"subscription": browser()})
        ana = User.objects.create_user("ana", password="x")
        self.client.force_login(ana)
        self.assertTrue(self.post("store_push_sync", {"subscription": browser()}).json()["orders"])
        self.assertEqual(PushSubscription.objects.get().user, ana)
        self.post("store_push_forget", {"endpoint": browser()["endpoint"]})
        self.assertFalse(PushSubscription.objects.exists())

    def test_a_finished_order_has_no_card(self):
        order = self.guest_order()
        Order.objects.filter(pk=order.pk).update(picked_up=True)
        self.assertNotContains(self.client.get(reverse("order_status", args=[order.id])), "nb-notify-sheet")


class OrderPushTests(MenuFixture, TestCase):
    """Order updates (online_store/order_push.py): each step once, to whoever follows the order."""
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def setUp(self):
        from push.models import PushSubscription
        self.webpush = mock.patch("push.send.webpush").start()
        mock.patch("push.send.background", lambda job: job()).start()
        self.addCleanup(mock.patch.stopall)
        self.ana = User.objects.create_user("ana", first_name="Ana", password="x")
        self.guest_phone = PushSubscription.objects.create(app="store", endpoint=browser()["endpoint"], p256dh="k", auth="a")
        self.anas_phone = PushSubscription.objects.create(app="store", user=self.ana, endpoint=browser(2)["endpoint"], p256dh="k", auth="a")

    def sent(self):
        out = [(call.args[0]["endpoint"], json.loads(call.args[1])) for call in self.webpush.call_args_list]
        self.webpush.reset_mock()
        return out

    def order(self, channel="web", **fields):
        from pos_server.orders import create_order
        with self.captureOnCommitCallbacks(execute=True):
            return create_order(lines=[self.dish(self.burger), self.dish(self.coffee)], channel=channel,
                                initial_status=OrderStation.STATUS_PENDING, name="Iryna", **fields)

    def step(self, order, codes, status):
        with self.captureOnCommitCallbacks(execute=True):
            order.set_station_status(codes, status)

    def test_a_pickup_order_step_by_step(self):
        order = self.order()
        order.push_subscriptions.add(self.guest_phone)
        self.step(order, ["kitchen"], OrderStation.STATUS_APPROVED)
        self.assertEqual(self.sent(), [], order.station_status_map())  # the bar hasn't taken it yet
        self.step(order, ["bar"], OrderStation.STATUS_APPROVED)
        ((endpoint, message),) = self.sent()
        self.assertEqual((endpoint, message["title"], message["url"], message["tag"]),
                         (self.guest_phone.endpoint, f"Order #{order.id} accepted", reverse("order_status", args=[order.id]), f"order-{order.id}"))
        with self.captureOnCommitCallbacks(execute=True):
            order.save()  # nothing new: nothing sent
        self.assertEqual(self.sent(), [])
        self.step(order, ["kitchen", "bar"], OrderStation.STATUS_DONE)
        self.assertEqual(self.sent()[0][1]["title"], f"Order #{order.id} is ready")
        with self.captureOnCommitCallbacks(execute=True):
            order.picked_up = True
            order.save()
        self.assertEqual(self.sent()[0][1]["title"], f"Order #{order.id} picked up")

    def test_a_customer_hears_on_their_devices_unless_they_turned_order_updates_off(self):
        order = self.order(customer=self.ana)
        self.step(order, ["kitchen", "bar"], OrderStation.STATUS_APPROVED)
        self.assertEqual([e for e, _m in self.sent()], [self.anas_phone.endpoint])
        self.anas_phone.order_updates = False
        self.anas_phone.save()
        order = self.order(customer=self.ana)
        self.step(order, ["kitchen", "bar"], OrderStation.STATUS_APPROVED)
        self.assertEqual(self.sent(), [])

    def test_the_admin_can_turn_order_updates_off(self):
        from push.models import NotificationSwitch
        NotificationSwitch.objects.create(kind="order_updates", enabled=False)
        order = self.order()
        order.push_subscriptions.add(self.guest_phone)
        self.step(order, ["kitchen", "bar"], OrderStation.STATUS_APPROVED)
        self.assertEqual(self.sent(), [])

    def test_a_delivery_on_its_way_and_delivered(self):
        courier = User.objects.create_user("kai", first_name="Kai", password="x")
        order = self.order(channel="delivery")
        order.push_subscriptions.add(self.guest_phone)
        delivery = Delivery.objects.create(order=order, destination="1 Main St", phone=6045550100, courier=courier)
        self.step(order, ["kitchen", "bar"], OrderStation.STATUS_APPROVED)
        self.assertIn("courier brings it", self.sent()[0][1]["body"])
        self.step(order, ["kitchen", "bar"], OrderStation.STATUS_DONE)
        self.assertEqual(self.sent(), [])  # a delivery's "ready" is for the courier
        with self.captureOnCommitCallbacks(execute=True):
            delivery.picked_up_at = timezone.now()
            delivery.save()
        message = self.sent()[0][1]
        self.assertEqual((message["title"], message["body"]), (f"Order #{order.id} is on its way", "Kai picked it up and is heading to you."))
        with self.captureOnCommitCallbacks(execute=True):
            delivery.delivered_at = timezone.now()
            delivery.save()
        self.assertEqual(self.sent()[0][1]["title"], f"Order #{order.id} delivered")

    def test_rejected_and_refunded(self):
        from online_store import order_push
        from online_store.models import RejectedOrder
        order = self.order()
        order.push_subscriptions.add(self.guest_phone)
        with self.captureOnCommitCallbacks(execute=True):
            order_push.refunded(order, mock.Mock(amount=Decimal("4.50")))
        self.assertEqual(self.sent()[0][1]["body"], "$4.50 is on its way back to you.")
        order_id = order.id
        with self.captureOnCommitCallbacks(execute=True):
            RejectedOrder.objects.create(order_id=order_id, reason="Out of buns")
            order.delete()
        ((endpoint, message),) = self.sent()
        self.assertEqual((endpoint, message["title"], message["body"]), (self.guest_phone.endpoint, f"We couldn’t take order #{order_id}", "Out of buns"))


class ScreenshotTests(TestCase):
    """The store app's screenshots (online_store/screenshots.py): retaken after Publish, the old set deleted."""

    def setUp(self):
        import shutil
        import tempfile
        from django.core.cache import cache
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = self.settings(MEDIA_ROOT=media, SCREENSHOT_URL="http://screenshots:3000", SCREENSHOT_SITE="http://web:8000",
                                 STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                                           "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
        override.enable()
        self.addCleanup(override.disable)
        cache.clear()

    def answer(self):
        return mock.Mock(content=b"\x89PNG fake", raise_for_status=lambda: None)

    def test_a_new_set_replaces_the_old_one(self):
        import requests
        from django.core.files.storage import default_storage
        from admin_portal.models import StoreSettings
        from online_store import screenshots
        with mock.patch("online_store.screenshots.requests.post", return_value=self.answer()) as post, \
                mock.patch("online_store.screenshots.time.time", return_value=1000):
            first = screenshots.refresh()
        self.assertEqual([s["form"] for s in first], ["narrow", "narrow", "wide"])
        self.assertEqual(post.call_args_list[0].kwargs["data"]["url"], "http://web:8000/store/")
        self.assertEqual(post.call_args_list[0].args[0], "http://screenshots:3000/forms/chromium/screenshot/url")
        with mock.patch("online_store.screenshots.requests.post", return_value=self.answer()), \
                mock.patch("online_store.screenshots.time.time", return_value=2000):
            second = screenshots.refresh()
        self.assertEqual(StoreSettings.load().app_screenshots, second)
        self.assertTrue(all(default_storage.exists(s["name"]) for s in second))
        self.assertFalse(any(default_storage.exists(s["name"]) for s in first))  # the old ones are gone
        with mock.patch("online_store.screenshots.requests.post", side_effect=requests.ConnectionError("down")):
            self.assertEqual(screenshots.refresh(), second)  # the server's down: the set stays
        self.assertEqual(sorted(default_storage.listdir("store_app")[1]), sorted(s["name"].split("/")[1] for s in second))

    def test_publishing_retakes_them_in_the_background_one_run_at_a_time(self):
        from online_store import screenshots
        with mock.patch("online_store.screenshots.background") as background, self.captureOnCommitCallbacks(execute=True):
            screenshots.refresh_later()
            screenshots.refresh_later()  # while the first runs: one more run after it
        background.assert_called_once()
        runs = []

        def refresh():
            runs.append(1)
            if len(runs) == 1:
                screenshots.refresh_later()  # published again while this run goes
        with mock.patch("online_store.screenshots.refresh", refresh), self.captureOnCommitCallbacks(execute=True):
            screenshots._run()
        self.assertEqual(len(runs), 2)  # the run, then the one asked for while it ran
