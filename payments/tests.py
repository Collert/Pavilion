"""Square Terminal payments (payments/square.py): a Terminal per register, checkouts kept in the database and updated
by Square's webhook, and an order claiming its card payment once. Square itself is always mocked."""
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from payments import square
from payments.models import SquareTerminal, TerminalCheckout
from pos_server.devices import DEVICE_COOKIE, set_device_cookie
from pos_server.models import CashDrawerSession, Dish, EligibleDevice, Order, Station
from users.testing import crew


def ok(body):
    return mock.Mock(is_success=lambda: True, body=body, errors=None)


def started(checkout_id, status="PENDING"):
    return ok({"checkout": {"id": checkout_id, "status": status}})


class RegistersAndTerminals(TestCase):
    """Two registers, each with its own paired Terminal."""

    @classmethod
    def setUpTestData(cls):
        cls.front = EligibleDevice.objects.create(name="Front register")
        cls.patio = EligibleDevice.objects.create(name="Patio register")
        cls.front_terminal = SquareTerminal.objects.create(name="Front", register=cls.front, device_code_id="dc-1", code="AAAA",
                                                           status="PAIRED", device_id="device-front")
        cls.patio_terminal = SquareTerminal.objects.create(name="Patio", register=cls.patio, device_code_id="dc-2", code="BBBB",
                                                           status="PAIRED", device_id="device-patio")
        cls.staff = crew(User.objects.create_user("dana", password="x", is_staff=True, first_name="Dana"))

    def client_on(self, register):
        """A signed-in client on `register` (its signed device cookie, as the app sets it)."""
        client = self.client_class()
        client.force_login(self.staff)
        response = set_device_cookie(HttpResponse(), register.token)
        client.cookies[DEVICE_COOKIE] = response.cookies[DEVICE_COOKIE].value
        return client


class CheckoutTests(RegistersAndTerminals):

    @mock.patch("payments.square.gather_terminal_checkout")
    def test_each_register_charges_on_its_own_terminal(self, gather):
        gather.side_effect = [started("c-front"), started("c-patio")]
        a = square.start_checkout(self.front, Decimal("12.50"))
        b = square.start_checkout(self.patio, Decimal("4.00"))
        self.assertEqual([c.args[1] for c in gather.call_args_list], ["device-front", "device-patio"])
        self.assertEqual((a.register, a.terminal, b.register, b.terminal), (self.front, self.front_terminal, self.patio, self.patio_terminal))
        # Square reports one; only that checkout changes
        square.checkout_event({"id": "c-patio", "status": "COMPLETED", "payment_ids": ["p1"]})
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.status, b.status, b.payment_ids), ("PENDING", "COMPLETED", ["p1"]))
        self.assertIsNone(square.checkout_event({"id": "someone-elses", "status": "COMPLETED"}))

    @override_settings(SQUARE_DEVICE_ID="")
    def test_a_register_without_a_terminal_is_told_to_pair_one(self):
        bar = EligibleDevice.objects.create(name="Bar register")
        with self.assertRaises(square.SquareError) as e:
            square.start_checkout(bar, Decimal("5"))
        self.assertIn("No card terminal", str(e.exception))
        with override_settings(SQUARE_DEVICE_ID="store-default"), mock.patch("payments.square.gather_terminal_checkout", return_value=started("c-bar")) as gather:
            square.start_checkout(bar, Decimal("5"))
        self.assertEqual(gather.call_args.args[1], "store-default")

    def test_an_order_claims_its_card_payment_once(self):
        row = TerminalCheckout.objects.create(checkout_id="c1", register=self.front, amount=Decimal("12.50"), status="IN_PROGRESS")
        order = Order.objects.create(name="Test")
        for register, amount in ((self.front, "12.50"), (self.patio, "12.50"), (self.front, "10.00")):
            with self.assertRaises(ValueError):
                square.claim_checkout("c1", register, Decimal(amount), order)  # not through yet, other register, other amount
            row.status = "COMPLETED"
            row.save()
        square.claim_checkout("c1", self.front, Decimal("12.50"), order)
        with self.assertRaises(ValueError) as e:
            square.claim_checkout("c1", self.front, Decimal("12.50"), order)
        self.assertIn("already used", str(e.exception))


@override_settings(SQUARE_WEBHOOK_SIGNATURE_KEY="signing-key")
class WebhookTests(RegistersAndTerminals):

    def webhook(self, event):
        with mock.patch("pos_server.views.is_valid_webhook_event_signature", return_value=True):
            return self.client.post(reverse("square_webhook"), json.dumps(event), content_type="application/json",
                                    HTTP_X_SQUARE_HMACSHA256_SIGNATURE="signed")

    def test_checkout_updates_are_kept_in_the_database(self):
        row = TerminalCheckout.objects.create(checkout_id="c9", register=self.front, amount=Decimal("3"))
        with mock.patch("payments.square.publish") as publish:
            self.webhook({"type": "terminal.checkout.updated", "data": {"object": {"checkout": {"id": "c9", "status": "COMPLETED"}}}})
        row.refresh_from_db()
        self.assertEqual(row.status, "COMPLETED")
        publish.assert_called_with("checkout")

    def test_a_typed_code_pairs_the_register_and_replaces_its_old_terminal(self):
        new = SquareTerminal.objects.create(name="New front", register=self.front, device_code_id="dc-new", code="CCCC")
        self.webhook({"type": "device.code.paired", "data": {"object": {"device_code": {"id": "dc-new", "status": "PAIRED", "device_id": "device-new"}}}})
        self.assertEqual(SquareTerminal.for_register(self.front), new)
        self.front_terminal.refresh_from_db()
        self.assertIsNone(self.front_terminal.register)

    def test_a_bad_signature_is_refused(self):
        response = self.client.post(reverse("square_webhook"), "{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)


class RegisterEndpointTests(RegistersAndTerminals):

    def test_a_register_only_sees_its_own_checkout(self):
        TerminalCheckout.objects.create(checkout_id="c-front", register=self.front, amount=Decimal("5"), status="COMPLETED")
        url = reverse("register_card_status", args=["c-front"])
        self.assertEqual(self.client_on(self.front).get(url).json()["status"], "COMPLETED")
        self.assertEqual(self.client_on(self.patio).get(url).status_code, 404)

    @mock.patch("payments.square.square_client")
    def test_check_with_square_and_cancel(self, client):
        TerminalCheckout.objects.create(checkout_id="c1", register=self.front, amount=Decimal("5"))
        front = self.client_on(self.front)
        url = reverse("register_card_status", args=["c1"])
        client.terminal.get_terminal_checkout.return_value = started("c1", "IN_PROGRESS")
        self.assertEqual(front.get(url, {"refresh": "1"}).json()["status"], "IN_PROGRESS")
        client.terminal.cancel_terminal_checkout.return_value = ok({"checkout": {"id": "c1", "status": "CANCELED", "cancel_reason": "SELLER_CANCELED"}})
        data = front.post(url, json.dumps({"op": "cancel"}), content_type="application/json").json()
        self.assertEqual((data["status"], data["finished"], data["reason"]), ("CANCELED", True, "SELLER_CANCELED"))
        client.terminal.cancel_terminal_checkout.assert_called_with("c1")

    @mock.patch("payments.square.square_client")
    def test_pairing_from_the_register(self, client):
        bar = EligibleDevice.objects.create(name="Bar register")
        on_bar = self.client_on(bar)
        url = reverse("register_terminal")
        self.assertEqual(on_bar.get(url).json()["paired"], None)
        client.devices.create_device_code.return_value = ok({"device_code": {"id": "dc-bar", "code": "QWER", "status": "UNPAIRED"}})
        state = on_bar.post(url, json.dumps({"action": "pair"}), content_type="application/json").json()
        self.assertEqual(state["code"]["code"], "QWER")
        client.devices.get_device_code.return_value = ok({"device_code": {"id": "dc-bar", "status": "PAIRED", "device_id": "device-bar"}})
        state = on_bar.post(url, json.dumps({"action": "check"}), content_type="application/json").json()
        self.assertEqual((state["paired"]["name"], state["code"]), ("Bar register", None))
        state = on_bar.post(url, json.dumps({"action": "unpair"}), content_type="application/json").json()
        self.assertIsNone(state["paired"])

    @mock.patch("payments.square.fill_card_details")
    def test_placing_an_order_with_a_card_payment(self, _details):
        kitchen = Station.objects.get_or_create(code="kitchen", defaults={"name": "Kitchen"})[0]
        tea = Dish.objects.create(title="Tea", price=Decimal("3.00"), station=kitchen)
        front = self.client_on(self.front)
        cart = {"lines": [{"type": "dish", "id": tea.id, "qty": 1}], "total": "3.00"}
        post = lambda payments: front.post(reverse("pos"), json.dumps({**cart, "payments": payments}), content_type="application/json")  # noqa: E731
        self.assertEqual(post([{"method": "card", "amount": "3.00"}]).status_code, 400)  # card payments come from the Terminal
        TerminalCheckout.objects.create(checkout_id="c-tea", register=self.front, amount=Decimal("3.00"), status="IN_PROGRESS")
        self.assertEqual(post([{"method": "card", "amount": "3.00", "checkoutId": "c-tea"}]).status_code, 400)  # not through yet
        TerminalCheckout.objects.filter(checkout_id="c-tea").update(status="COMPLETED")
        self.assertEqual(post([{"method": "card", "amount": "3.00", "checkoutId": "c-tea"}]).status_code, 200)
        order = Order.objects.get()
        self.assertEqual(TerminalCheckout.objects.get(checkout_id="c-tea").order, order)
        response = post([{"method": "card", "amount": "3.00", "checkoutId": "c-tea"}])
        self.assertEqual(response.status_code, 400)  # used once
        self.assertEqual(Order.objects.count(), 1)
        self.assertFalse(CashDrawerSession.objects.exists())


class DeviceLinkTests(TestCase):
    def test_a_device_token_link_registers_the_browser(self):
        device = EligibleDevice.objects.create(name="Register 2")
        self.client.force_login(User.objects.create_user("dana", is_staff=True))
        response = self.client.get(reverse("device_elig"), {"device-token": str(device.token)})
        self.assertRedirects(response, reverse("pos"), fetch_redirect_response=False)
        self.assertIn(DEVICE_COOKIE, response.cookies)
        self.assertNotIn(DEVICE_COOKIE, self.client.get(reverse("device_elig"), {"device-token": "nope"}).cookies)


@override_settings(SQUARE_APPLICATION_ID="sq0idp-app", SQUARE_APPLICATION_SECRET="sq0csp-secret", SQUARE_ENVIRONMENT="sandbox",
                   SQUARE_ACCESS_TOKEN="env-token")
class SquareOAuthTests(TestCase):
    """Connecting the store's own Square account (payments/square_oauth.py; the admin's Payments page)."""

    def setUp(self):
        self.owner = User.objects.create_superuser("owner", password="x")
        self.client.force_login(self.owner)

    def connect(self):
        """Press Connect with Square, then come back from Square with a code."""
        from urllib.parse import parse_qs, urlparse
        from payments.models import SquareConnection
        start = self.client.post(reverse("admin_payments"), {"op": "connect"})
        query = parse_qs(urlparse(start["Location"]).query)
        app = mock.Mock()
        app.o_auth.obtain_token.return_value = ok({"access_token": "seller-token", "refresh_token": "seller-refresh",
                                                   "expires_at": "2026-11-23T00:00:00Z", "merchant_id": "M1"})
        seller = mock.Mock()
        seller.merchants.retrieve_merchant.return_value = ok({"merchant": {"business_name": "Corner Kitchen"}})
        seller.locations.list_locations.return_value = ok({"locations": [{"id": "L1", "name": "Main Street", "status": "ACTIVE"}]})
        with mock.patch("payments.square_oauth._app_client", return_value=app), mock.patch("payments.square_oauth.seller_client", return_value=seller):
            back = self.client.get(reverse("square_oauth_callback"), {"code": "the-code", "state": query["state"][0]}, follow=True)
        return start, query, back, app, SquareConnection.current()

    def test_connect_with_square(self):
        from payments.secrets import decrypt
        start, query, back, app, connection = self.connect()
        self.assertTrue(start["Location"].startswith("https://connect.squareupsandbox.com/oauth2/authorize?"))
        self.assertEqual(query["client_id"], ["sq0idp-app"])
        self.assertIn("DEVICE_CREDENTIAL_MANAGEMENT", query["scope"][0])
        self.assertTrue(query["redirect_uri"][0].endswith("/square/oauth/callback"))
        self.assertContains(back, "Connected to Corner Kitchen on Square.")
        self.assertEqual(app.o_auth.obtain_token.call_args.args[0]["code"], "the-code")
        self.assertEqual((connection.merchant_id, connection.location_id, connection.location_name), ("M1", "L1", "Main Street"))
        self.assertNotIn("seller-token", connection.access_token)  # kept encrypted
        self.assertEqual(decrypt(connection.access_token), "seller-token")
        self.assertEqual(square.location_id(), "L1")

    def test_square_calls_use_the_connected_account(self):
        self.connect()
        with mock.patch("payments.square_oauth.seller_client") as seller:
            square.square_client.payments  # noqa: B018
        self.assertEqual(seller.call_args.args[0], "seller-token")

    def test_without_a_connection_the_environment_token_is_used(self):
        with mock.patch("payments.square_oauth.seller_client") as seller:
            square.square_client.payments  # noqa: B018
        self.assertEqual(seller.call_args.args[0], "env-token")

    def test_the_connect_button_is_a_real_page_load(self):
        # live-page would send it in the background, and the browser can't follow the redirect to Square from there
        self.assertContains(self.client.get(reverse("admin_payments")), '<form method="post" data-navigate>')

    def test_a_wrong_state_is_refused(self):
        from payments.models import SquareConnection
        self.client.post(reverse("admin_payments"), {"op": "connect"})
        back = self.client.get(reverse("square_oauth_callback"), {"code": "c", "state": "forged"}, follow=True)
        self.assertContains(back, "expired")
        self.assertIsNone(SquareConnection.current())

    def test_an_access_token_running_out_is_renewed(self):
        import datetime
        from django.utils import timezone
        from payments import square_oauth
        from payments.secrets import decrypt
        _s, _q, _b, _a, connection = self.connect()
        connection.expires_at = timezone.now() + datetime.timedelta(days=2)
        connection.save()
        app = mock.Mock()
        app.o_auth.obtain_token.return_value = ok({"access_token": "new-token", "expires_at": "2026-12-23T00:00:00Z"})
        with mock.patch("payments.square_oauth._app_client", return_value=app):
            self.assertEqual(square_oauth.access_token(connection), "new-token")
        self.assertEqual(app.o_auth.obtain_token.call_args.args[0]["grant_type"], "refresh_token")
        connection.refresh_from_db()
        self.assertEqual(decrypt(connection.refresh_token), "seller-refresh")  # kept when Square doesn't send a new one

    def test_disconnect_revokes_access(self):
        from payments.models import SquareConnection
        self.connect()
        app = mock.Mock()
        app.o_auth.revoke_token.return_value = ok({"success": True})
        with mock.patch("payments.square_oauth._app_client", return_value=app):
            self.client.post(reverse("admin_payments"), {"op": "disconnect"})
        self.assertEqual(app.o_auth.revoke_token.call_args.args, ({"client_id": "sq0idp-app", "access_token": "seller-token"}, "Client sq0csp-secret"))
        self.assertIsNone(SquareConnection.current())

    def test_only_people_who_can_edit_payments_connect(self):
        viewer = crew(User.objects.create_user("viewer", password="x", is_staff=True), "app.admin", "admin.payments.view")
        self.client.force_login(viewer)
        self.assertContains(self.client.get(reverse("admin_payments")), "Connect with Square")
        refused = self.client.post(reverse("admin_payments"), {"op": "connect"})
        self.assertFalse(refused.get("Location", "").startswith("https://connect."))
        self.assertNotEqual(self.client.get(reverse("square_oauth_callback"), {"code": "c", "state": "s"})["Location"], reverse("admin_payments"))

    @override_settings(SQUARE_APPLICATION_SECRET="")
    def test_the_page_says_when_the_app_isnt_set_up(self):
        self.assertContains(self.client.get(reverse("admin_payments")), "SQUARE_APPLICATION_SECRET")


@override_settings(SQUARE_ENVIRONMENT="sandbox")
class SandboxTerminalTests(RegistersAndTerminals):
    """Square's sandbox has no Terminals to pair: a register can use one of its test device ids instead."""

    def test_pick_a_test_terminal(self):
        bar = EligibleDevice.objects.create(name="Bar register")
        on_bar = self.client_on(bar)
        url = reverse("register_terminal")
        state = on_bar.get(url).json()
        self.assertIn("9fa747a2-25ff-48ee-b078-04381f7c828f", [d["id"] for d in state["sandbox"]])
        state = on_bar.post(url, json.dumps({"action": "sandbox", "device": "841100b9-ee60-4537-9bcf-e30b2ba5e215"}),
                            content_type="application/json").json()
        self.assertEqual(state["paired"]["device"], "841100b9-ee60-4537-9bcf-e30b2ba5e215")
        with mock.patch("payments.square.gather_terminal_checkout", return_value=started("c-test")) as gather:
            square.start_checkout(bar, Decimal("5"))
        self.assertEqual(gather.call_args.args[1], "841100b9-ee60-4537-9bcf-e30b2ba5e215")
        refused = on_bar.post(url, json.dumps({"action": "sandbox", "device": "made-up"}), content_type="application/json")
        self.assertEqual(refused.status_code, 502)

    @override_settings(SQUARE_ENVIRONMENT="production")
    def test_not_in_production(self):
        self.assertEqual(self.client_on(self.front).get(reverse("register_terminal")).json()["sandbox"], [])
        with self.assertRaises(square.SquareError):
            square.use_sandbox_terminal(self.front, "9fa747a2-25ff-48ee-b078-04381f7c828f")
