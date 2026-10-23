"""Tests for buying gift cards and the card API."""
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from payments.models import Transaction
from .models import GiftCard


@mock.patch("gift_cards.views.send_email")
class BuyCardTests(TestCase):
    def buy(self, transaction, amount="500"):
        return self.client.post(reverse("get-card"), {
            "transaction_uuid": str(transaction.uuid), "image": "files/gift-cards/presets/25.png",
            "email": "someone@example.com", "amount": amount, "name": "Someone", "sender": "", "gifted": "false"})

    def test_card_is_worth_what_was_paid(self, send):
        response = self.buy(Transaction.objects.create(amount=Decimal("25.00"), successful=True), amount="500")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(GiftCard.objects.get().available_balance, Decimal("25.00"))
        send.assert_called_once()

    def test_unpaid_transaction_creates_no_card(self, send):
        response = self.buy(Transaction.objects.create(amount=Decimal("25.00"), successful=False))
        self.assertEqual(response.status_code, 402)
        self.assertFalse(GiftCard.objects.exists())

    def test_transaction_buys_one_card(self, send):
        transaction = Transaction.objects.create(amount=Decimal("25.00"), successful=True)
        self.buy(transaction)
        self.buy(transaction)
        self.assertEqual(GiftCard.objects.count(), 1)

    def test_email_failure_still_shows_the_card(self, send):
        send.side_effect = Exception("listmonk down")
        response = self.buy(Transaction.objects.create(amount=Decimal("10.00"), successful=True))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(GiftCard.objects.exists())


class CardApiTests(TestCase):
    def setUp(self):
        self.card = GiftCard.objects.create(available_balance=Decimal("10.00"), email="owner@example.com")
        self.url = reverse("card_api", args=[self.card.number])

    def test_anyone_can_check_a_balance_but_not_the_email(self):
        data = self.client.get(self.url).json()
        self.assertEqual(Decimal(str(data["available_balance"])), Decimal("10.00"))
        self.assertNotIn("email", data)

    def test_only_staff_can_charge(self):
        body = json.dumps({"amount": "3.50"})
        self.assertEqual(self.client.post(self.url, body, content_type="application/json").status_code, 403)
        self.client.force_login(User.objects.create_user("staff", password="x", is_staff=True))
        self.assertEqual(self.client.post(self.url, body, content_type="application/json").status_code, 200)
        self.card.refresh_from_db()
        self.assertEqual(self.card.available_balance, Decimal("6.50"))

    def test_cannot_overdraw(self):
        self.client.force_login(User.objects.create_user("staff", password="x", is_staff=True))
        response = self.client.post(self.url, json.dumps({"amount": "10.01"}), content_type="application/json")
        self.assertEqual(response.status_code, 402)


class GiftCardNumberTests(TestCase):
    """Only a gift card's last 4 and a hash of its number are kept (models.GiftCard, giftcards.py)."""
    @classmethod
    def setUpTestData(cls):
        from pos_server.tests import MenuFixture
        MenuFixture.build_menu.__func__(cls)

    def test_the_number_is_shown_once_and_found_by_hash(self):
        card = GiftCard.objects.create(available_balance=Decimal("20.00"))
        number = card.number
        self.assertEqual((len(number), card.last4, card.original), (16, number[-4:], Decimal("20.00")))
        stored = GiftCard.objects.get(pk=card.pk)
        self.assertEqual(stored.number, "")
        self.assertNotIn(number, stored.number_hash)
        spaced = " ".join(number[i:i + 4] for i in range(0, 16, 4))
        self.assertEqual(GiftCard.find(spaced), stored)
        self.assertIsNone(GiftCard.find("1234 5678 9012 3456"))
        self.assertEqual(self.client.get(reverse("card_api", args=[number])).json()["last4"], number[-4:])

    def test_frozen_cards_cant_pay_and_history_is_kept(self):
        from pos_server.models import OrderStation
        from pos_server.orders import OrderError, create_order, refund_gift_cards
        from .giftcards import set_frozen
        from .models import GiftCardEvent
        card = GiftCard.objects.create(available_balance=Decimal("20.00"))
        number = card.number
        line = {"type": "dish", "id": self.coffee.id, "qty": 1, "cust": {}}
        set_frozen(card, True, None)
        with self.assertRaisesMessage(OrderError, "is frozen"):
            create_order(lines=[line], channel="store", initial_status=OrderStation.STATUS_APPROVED, gift_card_payments=[(number, "2.50")])
        set_frozen(card, False, None)
        order = create_order(lines=[line], channel="store", initial_status=OrderStation.STATUS_APPROVED, gift_card_payments=[(number, "2.50")])
        refund_gift_cards(order)
        self.assertEqual([(e.kind, e.amount) for e in card.events.exclude(kind=GiftCardEvent.NOTE)],
                         [("use", Decimal("-2.50")), ("refund", Decimal("2.50"))])

    def test_empty_cards_go_after_30_days_and_orders_keep_their_payment(self):
        import datetime
        from django.utils import timezone
        from pos_server.models import OrderStation
        from pos_server.orders import create_order
        from .giftcards import cleanup
        card = GiftCard.objects.create(available_balance=Decimal("2.50"))
        line = {"type": "dish", "id": self.coffee.id, "qty": 1, "cust": {}}
        order = create_order(lines=[line], channel="store", initial_status=OrderStation.STATUS_APPROVED, gift_card_payments=[(card.number, "2.50")])
        card.refresh_from_db()
        self.assertEqual(card.empty_since, timezone.localdate())
        self.assertEqual(cleanup(timezone.localdate() + datetime.timedelta(days=29)), 0)
        self.assertEqual(cleanup(timezone.localdate() + datetime.timedelta(days=30)), 1)
        auth = order.gift_card_auth.get()
        self.assertEqual((auth.card, auth.charged_balance), (None, Decimal("2.50")))

    def test_the_balance_link_has_no_number(self):
        from django.test import RequestFactory
        from .giftcards import balance_link
        card = GiftCard.objects.create(available_balance=Decimal("12.00"))
        link = balance_link(RequestFactory().get("/"), card)
        self.assertNotIn(card.number, link)
        page = self.client.get(link[link.index("/", 8):])
        self.assertContains(page, "$12.00")
        self.assertEqual(self.client.get(reverse("gift_balance", args=["nope"])).status_code, 404)




class StaffCardTests(TestCase):
    """gift_cards/staff_cards.py: refills, rules, discounts and balances on orders."""

    @classmethod
    def setUpTestData(cls):
        import datetime
        from pos_server.models import Component, ComponentIngredient, Dish, DishComponent, Ingredient, Station
        from pos_server.tests import MenuFixture
        MenuFixture.build_menu.__func__(cls)
        lager = Ingredient.objects.create(title="Lager", unit_of_measurement="ml", alcohol=True)
        pint = Component.objects.create(title="Pint of lager", unit_of_measurement="ea", crafting_option="buy", inventory=50)
        ComponentIngredient.objects.create(component=pint, ingredient=lager, quantity=473)
        cls.beer = Dish.objects.create(title="Lager", price=Decimal("8.00"), station=Station.objects.get(code="bar"))
        DishComponent.objects.create(dish=cls.beer, component=pint, quantity=1)
        cls.dana = User.objects.create_user("dana", first_name="Dana", last_name="Kim", is_staff=True)
        cls.cashier = User.objects.create_user("lee", first_name="Lee", is_staff=True)
        cls.yesterday = datetime.timedelta(days=1)

    def line(self, dish, qty=1):
        return {"type": "dish", "id": dish.id, "qty": qty, "cust": {}}

    def card(self, **fields):
        from .models import StaffCard, StaffCardType
        kind = fields.pop("kind", StaffCardType.BALANCE)
        t = StaffCardType.objects.create(name=fields.pop("name", "Shift meal"), kind=kind, channels=fields.pop("channels", ["store", "pickup"]), **fields)
        return StaffCard.objects.create(type=t, user=self.dana, number=StaffCard.new_number(), balance=t.amount if kind == "balance" else 0)

    def test_refills(self):
        import datetime
        from django.utils import timezone
        from . import staff_cards
        card = self.card(amount=Decimal("12"))
        card.balance = Decimal("4.50")
        card.refilled_at = timezone.now() - self.yesterday
        staff_cards.refill(card)
        self.assertEqual(card.balance, Decimal("12"))  # resets
        carry = self.card(name="Comp", amount=Decimal("75"), refill="week", refill_weekday=timezone.localdate().weekday(),
                          leftover="carry", cap=Decimal("150"))
        carry.balance, carry.refilled_at = Decimal("100"), timezone.now() - datetime.timedelta(days=8)
        staff_cards.refill(carry)
        self.assertEqual(carry.balance, Decimal("150"))  # 100 + 75 + 75, capped
        once = self.card(name="Training", amount=Decimal("20"), refill="never")
        once.refilled_at = timezone.now() - datetime.timedelta(days=30)
        once.balance = Decimal("0")
        staff_cards.refill(once)
        self.assertEqual(once.balance, Decimal("0"))
        self.assertEqual(staff_cards.problem(once, "store"), "This card is used up.")

    def test_a_balance_card_pays_but_not_for_alcohol_and_rejecting_puts_it_back(self):
        from pos_server.models import OrderPayment, OrderStation
        from pos_server.orders import OrderError, create_order, refund_gift_cards
        card = self.card(amount=Decimal("12"))
        order = create_order(lines=[self.line(self.burger), self.line(self.beer)], channel="store", initial_status=OrderStation.STATUS_APPROVED,
                             staff_cards=[card], payments=[{"method": "card", "amount": "10.50"}], cashier=self.cashier)
        self.assertEqual(order.name, "Dana Kim")  # their own order
        self.assertEqual(order.payments.get(method=OrderPayment.STAFF).amount, Decimal("12.00"))
        card.refresh_from_db()
        self.assertEqual(card.balance, Decimal("0.00"))
        with self.assertRaises(OrderError):  # empty now
            create_order(lines=[self.line(self.coffee)], channel="store", initial_status=OrderStation.STATUS_APPROVED, staff_cards=[card],
                         payments=[{"method": "card", "amount": "2.50"}])
        refund_gift_cards(order)  # what rejecting an order does
        card.refresh_from_db()
        self.assertEqual(card.balance, Decimal("12.00"))
        # Only the beer: the card can't pay any of it
        with self.assertRaises(OrderError):
            create_order(lines=[self.line(self.beer)], channel="store", initial_status=OrderStation.STATUS_APPROVED, staff_cards=[card],
                         staff_card_amount="1.00", payments=[{"method": "card", "amount": "7.00"}])

    def test_a_discount_card(self):
        from pos_server.models import Discount, OrderStation
        from pos_server.orders import OrderError, create_order
        from pos_server.pricing import price_cart
        card = self.card(name="Staff discount", kind="discount", discount_kind="pct", discount_value=Decimal("30"),
                         discount_cap=Decimal("4.00"), uses="day")
        cart = price_cart([self.line(self.coffee, 2), self.line(self.beer)], staff_cards=[card])
        self.assertEqual(cart.staff_discount.amount, Decimal("1.50"))  # 30% of the coffees; the lager isn't covered
        self.assertEqual(price_cart([self.line(self.burger, 2)], staff_cards=[card]).staff_discount.amount, Decimal("4.00"))  # capped
        Discount.objects.create(name="Half off", code="HALF", kind="pct", value=Decimal("50"), scope="order", active=True)
        better = price_cart([self.line(self.coffee, 2)], order_discount={"code": "HALF"}, staff_cards=[card])
        self.assertEqual((better.staff_discount, better.order_discount.amount), (None, Decimal("2.50")))
        self.assertIn("take more off", better.staff_note)
        create_order(lines=[self.line(self.coffee)], channel="store", initial_status=OrderStation.STATUS_APPROVED, staff_cards=[card],
                     payments=[{"method": "card", "amount": "1.75"}])
        with self.assertRaisesMessage(OrderError, "already used Staff discount today"):
            price_cart([self.line(self.coffee)], staff_cards=[card])
        card.type.uses = "any"
        card.type.channels = ["store"]
        card.type.save()
        with self.assertRaisesMessage(OrderError, "doesn’t work on pickup orders"):
            price_cart([self.line(self.coffee)], channel="web", staff_cards=[card])
