"""Tests for the online store: checkout (server-side pricing, payment checks, opening hours), themes, account, services."""
import datetime
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from deliveries.models import Delivery
from events.models import Event, StoreHours
from gift_cards.models import GiftCard
from payments.models import PaymentAuthorization, Transaction
from pos_server.models import Order, OrderStation
from pos_server.tests import MenuFixture


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

    def test_paid_order_is_created_pending(self):
        transaction = self.paid_transaction("16.00")
        response = self.place(transaction)
        order = Order.objects.get()
        self.assertRedirects(response, reverse("order_status", args=[order.id]), fetch_redirect_response=False)
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
        self.assertRedirects(response, reverse("order_status", args=[order.id]), fetch_redirect_response=False)
        self.assertEqual((order.tip, order.total, order.name, order.email), (Decimal("1.60"), Decimal("17.60"), "Iryna", "iryna@example.com"))
        self.assertEqual(order.stations.count(), 1)
        self.assertEqual(self.client.session["cart"]["lines"], [])
        o = self.client.get(reverse("order_status", args=[order.id])).context["o"]
        self.assertEqual(o["totals"][-1], ("Tip", "$1.60"))

    def test_card_payment_opens_the_card_sheet(self):
        self.add_burger()
        page = self.client.post(reverse("store_cart"), {"name": "Iryna", "email": "iryna@example.com", "op": "place"}).context
        self.assertEqual(page["pay"]["amount"], Decimal("18.40"))  # 16.00 + 15% tip
        self.assertIn("confirmation_needed=1", page["pay"]["url"])
        self.assertFalse(Order.objects.exists())

    def test_first_scheduled_time_is_never_too_early(self):
        from unittest import mock
        from online_store.pages import _schedule
        now = timezone.now()
        at = now.replace(minute=now.minute // 5 * 5, second=50, microsecond=0)  # 20 min later is :x0:50, not a whole 5 minutes
        with mock.patch("django.utils.timezone.now", return_value=at):
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
        self.assertRedirects(self.client.get(reverse("account")), reverse("store_signin") + "?next=/en/account", fetch_redirect_response=False)
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
        self.assertRedirects(response, reverse("order_status", args=[order.id]), fetch_redirect_response=False)
        self.assertEqual(order.customer, self.user)
        self.assertEqual(PaymentAuthorization.objects.get().status, PaymentAuthorization.AUTHORIZED)
        self.assertEqual(order.payments.get(method="card").card_last4, "4242")
        page = self.client.get(reverse("account")).context
        self.assertEqual((page["rows"][0]["o"], page["d"]["number"]), (order, order.id))
        self.client.post(reverse("account_reorder", args=[order.id]))
        line = self.client.session["cart"]["lines"][0]
        self.assertEqual((line["id"], line["qty"], line["cust"]["options"][self.temp_key]), (self.burger.id, 2, self.well.id))
        self.assertEqual(self.client.get(reverse("account_receipt", args=[order.id])).status_code, 200)


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
