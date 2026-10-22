import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from payments import square
from pos_server.models import CateringPrice, Dish, Disposable, Ingredient, Station
from services.models import Booking, Service

from . import flow, payments, pricing
from .models import CateringLine, CateringMessage, CateringOrder, CateringSettings


class CateringFixture:
    @classmethod
    def build(cls):
        station = Station.objects.create(code="k", name="Kitchen", icon="burger")
        cls.burger = Dish.objects.create(title="Burger", price=Decimal("14.00"), station=station, prep_minutes=6,
                                         catering=True, catering_min=10)
        CateringPrice.objects.create(dish=cls.burger, quantity=10, price=Decimal("12.50"))
        CateringPrice.objects.create(dish=cls.burger, quantity=40, price=Decimal("11.00"))
        cls.server = Service.objects.create(title="Server", category=Service.STAFF, pricing=Service.HOUR, price=Decimal("38"),
                                            details={"staff": 6, "gratuity": 0})
        cls.chafer = Service.objects.create(title="Chafer", category=Service.EQUIPMENT, pricing=Service.DAY, price=Decimal("18"),
                                            deposit=True, deposit_amount=Decimal("40"), details={"owned": 10})
        cls.delivery = Service.objects.create(title="Delivery", category=Service.DELIVERY, pricing=Service.FLAT, price=Decimal("35"))
        clam = Ingredient.objects.create(title="Clamshell", unit_of_measurement="ea", pack_size=200, pack_price=Decimal("24.00"))
        cls.clam = Disposable.objects.create(ingredient=clam)
        cls.owner = User.objects.create_superuser("owner", password="x", first_name="Priya")

    def make_order(self, days=10, card="demo:card-1", **fields):
        order = CateringOrder.objects.create(
            name="Ben Carter", email="ben@example.com", title="Office lunch", event_date=timezone.localdate() + datetime.timedelta(days=days),
            event_time=datetime.time(12, 0), placed_at=timezone.now() - datetime.timedelta(days=1), square_customer_id="cust-1",
            square_card_id=card, card_brand="VISA", card_last4="4242", **fields)
        lines = [(CateringLine.DISH, self.burger, 20, 0, "12.50"), (CateringLine.SERVICE, self.server, 2, 4, "38"),
                 (CateringLine.SERVICE, self.chafer, 3, 1, "18"), (CateringLine.SERVICE, self.delivery, 1, 0, "35"),
                 (CateringLine.DISPOSABLE, self.clam, 60, 0, "0.75")]
        for position, (kind, item, quantity, amount, price) in enumerate(lines):
            CateringLine.objects.create(order=order, kind=kind, quantity=quantity, amount=amount, price=Decimal(price),
                                        position=position, **{CateringLine.FIELDS[kind]: item})
        return order


class PricingTests(CateringFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()

    @override_settings(TAX_RATE="0.05")
    def test_order_totals(self):
        t = pricing.order_totals(self.make_order())
        # Food 20 × 12.50; services 2 servers × 4 h × 38 + 3 chafers × 18 + 35 flat; 60 clamshells × 0.75
        self.assertEqual((t["food"], t["services"], t["extras"]), (Decimal("250.00"), Decimal("393.00"), Decimal("45.00")))
        self.assertEqual((t["tax"], t["total"], t["deposit"]), (Decimal("34.40"), Decimal("722.40"), Decimal("120")))
        self.assertEqual((t["minutes"], t["portions"]), (120, 20))

    def test_changes_and_prices(self):
        before = pricing.lines_of(self.make_order())
        after = [dict(line) for line in before]
        after[0]["quantity"] = 40
        after[4]["price"] = "0.50"
        after[3]["removed"] = True
        after.append({"id": "", "kind": "dish", "ref": self.burger.id, "quantity": 5, "amount": 0, "price": "14.00"})
        diff = pricing.changes(before, after, pricing.Catalog(before, after))
        self.assertEqual([kind for _text, kind in diff], ["ch", "rm", "ch", "add"])  # in line order
        self.assertEqual(diff[0][0], "Burger 20 → 40")
        self.assertEqual(pricing.catalog_price("dish", self.burger, 45), Decimal("11.00"))
        self.assertEqual(pricing.suggested_price("disposable", self.clam), Decimal("0.25"))  # 12¢ cost, twice, to the nickel


@override_settings(TAX_RATE="0")
@mock.patch("catering.flow.send_email")
class FlowTests(CateringFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()

    def test_accept_charges_books_and_schedules_the_hold(self, send):
        order = self.make_order(days=10)
        flow.accept(order, self.owner, "See you there")
        order.refresh_from_db()
        self.assertEqual((order.status, order.charged, order.hold_state), (CateringOrder.CONFIRMED, Decimal("688.00"), ""))
        self.assertTrue(order.payment_id.startswith(payments.DEMO))
        self.assertEqual(Booking.objects.filter(status=Booking.CONFIRMED, channel=Booking.CATERING).count(), 3)
        self.assertIn("deposit hold scheduled", order.messages.last().text)
        self.assertEqual(send.call_args.args[:2], ("catering_accepted", "ben@example.com"))

    @mock.patch("payments.square.charge_card", side_effect=square.SquareError([{"detail": "Card declined"}]))
    def test_a_declined_card_changes_nothing(self, charge, send):
        order = self.make_order(card="ccof:real")
        with self.assertRaises(payments.PaymentFailed):
            flow.accept(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.status, order.messages.count(), order.charged), (CateringOrder.NEW, 0, Decimal("0")))
        self.assertEqual(charge.call_args.args[:3], ("cust-1", "ccof:real", Decimal("688.00")))

    def test_propose_then_the_customer_agrees(self, send):
        order = self.make_order()
        draft = pricing.lines_of(order)
        draft[0]["quantity"] = 45
        draft[0]["price"] = "11.00"
        draft[4]["removed"] = True
        with self.assertRaises(flow.CateringError):
            flow.propose(order, self.owner, draft, "  ")
        flow.propose(order, self.owner, draft, "Bigger lunch, no clamshells")
        order.refresh_from_db()
        self.assertEqual((order.status, order.lines.count(), len(order.previous)), (CateringOrder.WAITING, 4, 5))
        message = order.messages.get(kind=CateringMessage.CHANGES)
        self.assertEqual((message.was_total, message.now_total), (Decimal("688.00"), Decimal("888.00")))
        flow.agree(order)
        order.refresh_from_db()
        self.assertEqual((order.status, order.charged, order.previous), (CateringOrder.CONFIRMED, Decimal("888.00"), None))

    @mock.patch("payments.square.cancel_payment")
    @mock.patch("payments.square.refund_payment", return_value="ref-1")
    @mock.patch("payments.square.charge_card", side_effect=["pay-1", "hold-1"])
    def test_cancelling_refunds_and_drops_the_hold(self, charge, refund, cancel, send):
        order = self.make_order(days=1, card="ccof:real")
        flow.accept(order, self.owner)  # the event is tomorrow, so the deposit is held right away
        order.refresh_from_db()
        self.assertEqual((order.hold_state, order.hold_amount, charge.call_args.kwargs["autocomplete"]), (CateringOrder.HELD, Decimal("120"), False))
        flow.reject(order, self.owner, "Other", "Kitchen flooded, so sorry")
        order.refresh_from_db()
        self.assertEqual((order.status, order.refunded, order.hold_state), (CateringOrder.CANCELLED, Decimal("688.00"), CateringOrder.RELEASED))
        self.assertEqual(refund.call_args.args[:2], ("pay-1", Decimal("688.00")))
        cancel.assert_called_once_with("hold-1")
        self.assertFalse(Booking.objects.filter(status=Booking.CONFIRMED).exists())

    def test_daily_holds_due_deposits_and_completes_past_orders(self, send):
        soon = self.make_order(days=1)
        flow.accept(soon, self.owner)
        CateringOrder.objects.filter(pk=soon.pk).update(hold_state="")  # as if accepted before the hold was due
        past = self.make_order(days=-2)
        past.lines.exclude(kind=CateringLine.DISH).delete()
        flow.accept(past, self.owner)
        self.assertEqual(flow.daily(), {"held": 1, "completed": 1, "failed": 0})
        self.assertEqual(CateringOrder.objects.get(pk=soon.pk).hold_state, CateringOrder.HELD)
        self.assertEqual(CateringOrder.objects.get(pk=past.pk).status, CateringOrder.DONE)

    def test_keeping_the_deposit(self, send):
        order = self.make_order(days=0)
        flow.accept(order, self.owner)
        flow.finish(order, self.owner, keep_deposit=True)
        order.refresh_from_db()
        self.assertEqual((order.status, order.hold_state), (CateringOrder.DONE, CateringOrder.KEPT))

    def test_new_orders_asking_for_less_notice_are_flagged(self, send):
        CateringSettings.objects.update_or_create(pk=1, defaults={"lead": 7, "auto_reply": "Thanks!"})
        order = self.make_order(days=3)
        flow.place(order)
        order.refresh_from_db()
        self.assertTrue(order.rush)
        self.assertEqual(order.messages.count(), 3)  # placed, the request note, the auto-reply
        self.assertEqual(send.call_args.args[0], "catering_received")


@override_settings(TAX_RATE="0")
@mock.patch("catering.flow.send_email")
class CustomerPageTests(CateringFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()

    def test_reply_and_accept_changes(self, send):
        order = self.make_order()
        url = reverse("catering_order", args=[order.token])
        self.assertContains(self.client.get(url), "Office lunch")
        self.client.post(url, {"op": "reply", "text": "Can we add napkins?"})
        self.assertEqual(order.messages.get().who, CateringMessage.CUSTOMER)
        draft = pricing.lines_of(order)
        draft[0]["quantity"] = 25
        flow.propose(order, self.owner, draft, "More burgers")
        self.assertContains(self.client.get(url), "Accept changes")
        self.client.post(url, {"op": "agree"})
        self.assertEqual(CateringOrder.objects.get(pk=order.pk).status, CateringOrder.CONFIRMED)
        self.assertEqual(self.client.get(reverse("catering_order", args=["00000000-0000-4000-8000-000000000000"])).status_code, 404)

    def test_customers_can_listen_for_changes(self, send):
        from asgiref.sync import async_to_sync
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory
        from pavilion.realtime import _can_listen
        request = RequestFactory().get("/events/catering")
        request.user = AnonymousUser()
        self.assertTrue(async_to_sync(_can_listen)(request, "catering"))
        self.assertFalse(async_to_sync(_can_listen)(request, "inventory"))


@override_settings(TAX_RATE="0")
@mock.patch("catering.flow.send_email")
class CounterTests(CateringFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()

    def test_versions_counter_and_cancel(self, send):
        order = self.make_order()
        flow.place(order)
        draft = pricing.lines_of(order)
        draft[0]["quantity"] = 30
        flow.propose(order, self.owner, draft, "More burgers")
        self.assertEqual([(v.number, v.by) for v in order.versions.all()], [(1, "customer"), (2, "staff")])
        url = reverse("catering_order", args=[order.token])
        page = self.client.get(url + "?edit=1").context
        lines = page["draft"]["lines"]
        fields = {"editing": "1", "line-count": len(lines), "add_kind": "dish", "add_ref": "", "add_qty": "10", "add_price": "", "add_last": "", "msg": "Fewer clamshells"}
        for i, line in enumerate(lines):
            fields.update({f"line-{i}-{k}": line[k] for k in ("id", "kind", "ref", "quantity", "amount", "price")})
            fields[f"line-{i}-removed"] = ""
        fields["line-4-removed"] = "1"  # no clamshells
        self.client.post(url, {**fields, "op": "send"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.lines.count(), order.versions.count()), (CateringOrder.NEW, 4, 3))
        self.assertEqual(order.messages.filter(kind=CateringMessage.COUNTER).get().changes[0][1], "rm")
        page = self.client.get(url + "?v=2").context
        self.assertEqual(page["chosen"], 2)
        self.client.post(url, {"op": "cancel"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.charged), (CateringOrder.CANCELLED, Decimal("0")))


@override_settings(TAX_RATE="0")
@mock.patch("catering.flow.send_email")
class QuoteTests(CateringFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()
        CateringSettings.objects.update_or_create(pk=1, defaults={"lead": 7, "min_subtotal": Decimal("200"), "rush": False})

    def test_build_and_send(self, send):
        url = reverse("store_catering")
        day = timezone.localdate() + datetime.timedelta(days=10)
        st = self.client.post(url, {"op": "ful:delivery"}).context["st"]
        self.assertEqual(st["blocker"], "Pick a date for your event.")
        self.assertTrue(all(c["off"] for c in st["cells"] if c and c["date"] < timezone.localdate() + datetime.timedelta(days=7)))
        self.client.post(url, {"op": f"date:{day.isoformat()}"})
        self.client.post(url, {"op": f"add:{self.burger.id}", "hour": "12", "minute": "00", "ampm": "PM", "address": "1 Main St",
                               "name": "Ben", "email": "ben@example.com", "title": "Lunch"})
        page = self.client.post(url, {"op": f"inc:{self.burger.id}"}).context
        q, st = page["q"], page["st"]
        self.assertEqual(q["dishes"], {str(self.burger.id): 15})
        self.assertEqual(q["services"], {str(self.delivery.id): {"q": 1, "h": 0}})  # delivery adds the fee
        self.assertEqual(st["totals"]["subtotal"], Decimal("222.50"))  # 15 × 12.50 + 35
        self.assertEqual(st["blocker"], "")
        self.client.post(url, {"op": "send"})
        self.assertFalse(CateringOrder.objects.exists())  # no card yet
        session = self.client.session
        session["saved_card"] = {"customer": "demo:cust", "id": "demo:card", "brand": "VISA", "last4": "1111"}
        session.save()
        self.client.post(url, {"op": "agree"})
        response = self.client.post(url, {"op": "send"})
        order = CateringOrder.objects.get()
        self.assertRedirects(response, reverse("catering_order", args=[order.token]), fetch_redirect_response=False)
        self.assertEqual((order.title, order.fulfilment, order.event_date, order.card_last4, order.lines.count()), ("Lunch", "delivery", day, "1111", 2))
        self.assertEqual(order.versions.count(), 1)
        self.assertNotIn("catering_quote", self.client.session)

    def test_delivery_adds_the_delivery_service(self, send):
        url = reverse("store_catering")
        Service.objects.filter(pk=self.delivery.id).update(catering=False, online=True)  # booked online counts too
        q = self.client.post(url, {"op": "ful:delivery"}).context["q"]
        self.assertEqual(q["services"], {str(self.delivery.id): {"q": 1, "h": 0}})
        q = self.client.post(url, {"op": "ful:pickup"}).context["q"]
        self.assertEqual(q["services"], {})  # pickup takes delivery off
        self.client.get(url + f"?add={self.delivery.id}")  # from the services page: they want it delivered
        q = self.client.get(url).context["q"]
        self.assertEqual((q["ful"], list(q["services"])), ("delivery", [str(self.delivery.id)]))
        self.assertEqual([w for w, _l, _d in self.client.get(url).context["fuls"]], ["pickup", "delivery"])
        Service.objects.filter(pk=self.delivery.id).update(active=False)
        self.client.post(url, {"op": "ful:pickup"})
        q = self.client.post(url, {"op": "ful:delivery"}).context["q"]
        self.assertEqual(q["services"], {})  # no delivery service: delivery is free
