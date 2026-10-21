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
