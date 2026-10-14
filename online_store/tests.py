"""Tests for online checkout (place_order): server-side pricing, payment checks and opening hours."""
import datetime
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from deliveries.models import Delivery
from events.models import Event
from gift_cards.models import GiftCard
from payments.models import PaymentAuthorization, Transaction
from pos_server.models import Order, OrderStation
from pos_server.tests import MenuFixture


class PlaceOrderTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def setUp(self):
        now = timezone.localtime()
        self.event = Event.objects.create(
            title="Today", start=now - datetime.timedelta(hours=1), end=now + datetime.timedelta(hours=2),
            restaurant_open=True, in_person_open=True, online_open=True, delivery_open=False)
        self.pickup_time = (now + datetime.timedelta(minutes=30)).strftime("%H:%M")
        if now.hour == 23 and now.minute >= 29:
            self.skipTest("Pickup time would roll over to tomorrow")

    def paid_transaction(self, amount):
        transaction = Transaction.objects.create(amount=Decimal(amount), successful=True)
        PaymentAuthorization.objects.create(payment_id=f"pay-{transaction.uuid}", amount=int(Decimal(amount) * 100),
                                            currency="CAD", transaction=transaction)
        return transaction

    def place(self, items, transaction=None, gift=(), method="pick-up", coupon="", **extra):
        cart = {"lines": items, "total": 0.01, "coupon": coupon,  # the browser's total is ignored
                "partialPayments": [{"type": "gift", "number": number, "amount": amount} for number, amount in gift]}
        data = {
            "cart-string": json.dumps(cart), "transaction_uuid": str(transaction.uuid) if transaction else "",
            "delivery-pickup-toggle": method, "name": "Iryna", "phone": "604-555-0123", "time": self.pickup_time,
            "order-instructions": "", "here-to-go-toggle": "go", **extra,
        }
        return self.client.post(reverse("submit_order"), data)

    def test_paid_order_is_created_pending(self):
        transaction = self.paid_transaction("16.50")
        response = self.place([self.dish(self.burger), self.dish(self.tea)], transaction)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.channel, "web")
        self.assertEqual(order.total, Decimal("16.50"))
        self.assertEqual(order.phone, 6045550123)
        self.assertEqual(set(order.station_status_map().values()), {OrderStation.STATUS_PENDING})
        self.assertEqual(order.authorization.transaction, transaction)
        transaction.refresh_from_db()
        self.assertTrue(transaction.used)

    def test_unpaid_transaction_is_refused(self):
        transaction = Transaction.objects.create(amount=Decimal("16.50"), successful=False)
        response = self.place([self.dish(self.burger), self.dish(self.tea)], transaction)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_underpaid_transaction_is_refused(self):
        transaction = self.paid_transaction("0.01")
        response = self.place([self.dish(self.burger)], transaction)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())
        transaction.refresh_from_db()
        self.assertFalse(transaction.used)

    def test_transaction_cannot_be_reused(self):
        transaction = self.paid_transaction("14.50")
        self.place([self.dish(self.burger)], transaction)
        response = self.place([self.dish(self.burger)], transaction)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 1)

    def test_gift_card_can_cover_everything(self):
        card = GiftCard.objects.create(available_balance=Decimal("20.00"))
        response = self.place([self.dish(self.burger)], gift=[(card.number, "14.50")])
        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.available_balance, Decimal("5.50"))

    def test_combo_and_coupon(self):
        from pos_server.models import Discount
        Discount.objects.create(name="Welcome", kind="pct", value=Decimal("10"), scope="order", code="WELCOME10", online=True)
        response = self.place([self.lunch(self.tea)], self.paid_transaction("14.85"), coupon="welcome10")
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual((order.subtotal, order.discount, order.total), (Decimal("16.50"), Decimal("1.65"), Decimal("14.85")))
        self.assertEqual(order.discounts.get().label, "WELCOME10")

    def test_quote(self):
        response = self.client.post(reverse("cart_quote"), json.dumps({"lines": [self.lunch()], "coupon": "NOPE"}),
                                    content_type="application/json")
        self.assertEqual(response.json()["total"], 1600)
        self.assertEqual(response.json()["orderDiscountError"], "That code isn't valid.")

    def test_menu_lists_combos(self):
        response = self.client.get(reverse("menu"))
        self.assertIn(f"c{self.combo.id}", response.context["store_items"])
        self.assertEqual(response.context["store_items"][f"c{self.combo.id}"]["ask"]["title"], "Drink")

    def test_closed_when_there_is_no_event(self):
        self.event.delete()
        response = self.place([self.dish(self.burger)], self.paid_transaction("14.50"))
        self.assertEqual(response.status_code, 400)

    def test_delivery_needs_delivery_open(self):
        response = self.place([self.dish(self.burger)], self.paid_transaction("14.50"), method="delivery",
                              **{"delivery-address-1": "1 Main St", "delivery-address-city": "New Westminster",
                                 "delivery-address-postal": "V3M 1A1", "delivery-dropoff-method": "door"})
        self.assertEqual(response.status_code, 400)
        self.event.delivery_open = True
        self.event.save()
        response = self.place([self.dish(self.burger)], self.paid_transaction("14.50"), method="delivery",
                              **{"delivery-address-1": "1 Main St", "delivery-address-city": "New Westminster",
                                 "delivery-address-postal": "V3M 1A1", "delivery-dropoff-method": "door"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Delivery.objects.get().order.channel, "delivery")

    def test_order_status_page(self):
        self.place([self.dish(self.burger)], self.paid_transaction("14.50"))
        order = Order.objects.get()
        response = self.client.get(reverse("order_status", kwargs={"id": order.id}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status"], 1)  # scheduled: the pickup time is 30 minutes from now
