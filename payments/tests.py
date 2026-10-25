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


class RepairingTests(RegistersAndTerminals):
    """Square Terminals keep the code they signed in with; Pavilion keeps one register per Terminal and says when a code expired."""

    def test_a_terminal_paired_again_leaves_its_old_register(self):
        new = SquareTerminal.objects.create(name="Patio", register=self.patio, device_code_id="dc-again", code="ZZZZ")
        square.device_code_event({"id": "dc-again", "status": "PAIRED", "device_id": "device-front"})  # the front's Terminal
        self.front_terminal.refresh_from_db()
        self.assertIsNone(self.front_terminal.register)
        self.assertEqual(SquareTerminal.for_register(self.patio), new)
        self.assertIsNone(SquareTerminal.for_register(self.front))

    def test_an_unused_code_expires(self):
        import datetime
        from django.utils import timezone
        bar = EligibleDevice.objects.create(name="Bar register")
        SquareTerminal.objects.create(name="Bar", register=bar, device_code_id="dc-old", code="OLDD",
                                      pair_by=timezone.now() - datetime.timedelta(minutes=1))
        state = self.client_on(bar).get(reverse("register_terminal")).json()
        self.assertEqual((state["code"]["code"], state["code"]["expired"]), ("OLDD", True))


class DeviceTests(TestCase):
    """Devices trusted to run staff screens: the setup page, kiosk links (?device-token= on any link) and the admin's Devices page."""

    def setUp(self):
        self.owner = User.objects.create_superuser("owner", password="pw", first_name="Alex")
        self.device = EligibleDevice.objects.create(name="Front register")

    def test_a_device_code_in_any_link_sets_the_browser_up(self):
        url = reverse("time_clock") + f"?device-token={self.device.token}&x=1"
        response = self.client.get(url)
        self.assertRedirects(response, reverse("time_clock") + "?x=1", fetch_redirect_response=False)  # the code leaves the address
        self.assertIn(DEVICE_COOKIE, response.cookies)
        self.assertNotIn(DEVICE_COOKIE, self.client_class().get(reverse("time_clock") + "?device-token=nope").cookies)

    def test_the_setup_page_sends_a_device_on(self):
        self.client.get(reverse("device_elig") + f"?device-token={self.device.token}")
        self.assertRedirects(self.client.get(reverse("device_elig")), reverse("pos"), fetch_redirect_response=False)

    def test_someone_who_can_add_devices_names_this_one(self):
        url = reverse("device_elig")
        page = self.client.get(url)
        self.assertContains(page, "Which device is this?")
        refused = self.client.post(url, {"op": "add", "name": "Patio", "username": "owner", "password": "wrong"})
        self.assertContains(refused, "don’t match")
        staff = User.objects.create_user("dana", password="pw", is_staff=True)
        self.assertContains(self.client.post(url, {"op": "add", "name": "Patio", "username": "dana", "password": "pw"}), "don’t let you add devices")
        done = self.client.post(url, {"op": "add", "name": "Patio", "username": "owner", "password": "pw"})
        self.assertContains(done, "This is Patio")
        self.assertEqual(str(EligibleDevice.objects.get(name="Patio").token), done.cookies[DEVICE_COOKIE].value.split(":")[0])
        del staff

    def test_join_an_existing_device_or_paste_its_code(self):
        url = reverse("device_elig")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url), "Front register")  # it's offered to pick
        done = self.client.post(url, {"op": "join", "device": self.device.id})
        self.assertContains(done, "This is Front register")
        anonymous = self.client_class()
        self.assertContains(anonymous.post(url, {"op": "code", "code": "nope"}), "isn’t one of this store’s devices")
        self.assertContains(anonymous.post(url, {"op": "code", "code": str(self.device.token)}), "This is Front register")

    def test_the_admin_devices_page(self):
        url = reverse("admin_devices")
        self.client.force_login(self.owner)
        page = self.client.get(url, {"sel": self.device.id})
        self.assertContains(page, str(self.device.token))
        self.assertContains(page, f"?device-token={self.device.token}")  # kiosk links
        self.client.post(url, {"op": "new", "new_name": "Kitchen screen"})
        added = EligibleDevice.objects.get(name="Kitchen screen")
        self.client.post(url, {"op": "rename", "sel": added.id, "name": "Pass screen"})
        old = added.token
        self.client.post(url, {"op": "newcode", "sel": added.id})
        added.refresh_from_db()
        self.assertEqual(added.name, "Pass screen")
        self.assertNotEqual(added.token, old)
        self.client.post(url, {"op": "delete", "sel": added.id})
        self.assertFalse(EligibleDevice.objects.filter(pk=added.pk).exists())

    def test_seeing_devices_isnt_changing_them(self):
        viewer = crew(User.objects.create_user("viewer", password="x", is_staff=True), "app.admin", "admin.devices.view")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("admin_devices")).status_code, 200)
        self.client.post(reverse("admin_devices"), {"op": "newcode", "sel": self.device.id})
        old = self.device.token
        self.device.refresh_from_db()
        self.assertEqual(self.device.token, old)



@override_settings(SQUARE_DEVICE_ID="", SQUARE_APPLICATION_ID="sq0idp-app", SQUARE_ENVIRONMENT="sandbox")
class PhonePaymentTests(RegistersAndTerminals):
    """A register without a Terminal: the customer pays by card on their own phone (QR code → /payments/pay/<link>)."""

    def setUp(self):
        self.bar = EligibleDevice.objects.create(name="Bar register")
        kitchen = Station.objects.get_or_create(code="kitchen", defaults={"name": "Kitchen"})[0]
        self.tea = Dish.objects.create(title="Tea", price=Decimal("3.00"), station=kitchen)
        self.on_bar = self.client_on(self.bar)
        self.cart = {"lines": [{"type": "dish", "id": self.tea.id, "qty": 2}], "total": "6.00"}

    def start(self):
        answer = self.on_bar.post(reverse("register_card"), json.dumps({**self.cart, "amount": "6.00", "payments": []}),
                                  content_type="application/json").json()
        return answer, TerminalCheckout.objects.get(checkout_id=answer["checkoutId"])

    def charge(self, row, client=None):
        client = client or self.client_class()
        return client.post(reverse("phone_pay", args=[row.link]), json.dumps({"token": "cnon:card"}), content_type="application/json")

    @mock.patch("payments.square.square_client")
    def test_pay_on_the_phone_then_place_the_order(self, client):
        answer, row = self.start()
        self.assertIn("/payments/pay/", answer["phone"]["url"])
        self.assertIn("<svg", answer["phone"]["qr"])
        self.assertEqual((row.kind, row.amount, row.summary[0]["title"], row.summary[0]["qty"]), ("phone", Decimal("6.00"), "Tea", 2))
        page = self.client_class().get(reverse("phone_pay", args=[row.link]))
        self.assertContains(page, "$6.00")
        self.assertContains(page, "Tea")
        client.payments.create_payment.return_value = ok({"payment": {"id": "pay-1"}})
        self.assertEqual(self.charge(row).json()["status"], "success")
        sent = client.payments.create_payment.call_args.args[0]
        self.assertEqual((sent["amount_money"]["amount"], sent["idempotency_key"]), (600, row.checkout_id))  # the register's amount, once
        self.assertEqual(self.on_bar.get(reverse("register_card_status", args=[row.checkout_id])).json()["status"], "COMPLETED")
        self.assertContains(self.client_class().get(reverse("phone_pay", args=[row.link])), "Paid $6.00")
        client.payments.get_payment.return_value = ok({"payment": {"card_details": {"card": {"card_brand": "VISA", "last_4": "4242"}}}})
        placed = self.on_bar.post(reverse("pos"), json.dumps({**self.cart, "payments": [{"method": "card", "amount": "6.00", "checkoutId": row.checkout_id}]}),
                                  content_type="application/json")
        self.assertEqual(placed.status_code, 200, placed.content)
        payment = Order.objects.get().payments.get()
        self.assertEqual((payment.square_payment_id, payment.card_last4), ("pay-1", "4242"))

    @mock.patch("payments.square.square_client")
    def test_a_canceled_phone_payment_cant_be_paid(self, client):
        _answer, row = self.start()
        canceled = self.on_bar.post(reverse("register_card_status", args=[row.checkout_id]), json.dumps({"op": "cancel"}),
                                    content_type="application/json").json()
        self.assertEqual(canceled["status"], "CANCELED")
        self.assertEqual(self.charge(row).status_code, 402)
        client.payments.create_payment.assert_not_called()
        self.assertContains(self.client_class().get(reverse("phone_pay", args=[row.link])), "This payment is closed")

    def test_with_a_terminal_it_goes_to_the_terminal(self):
        SquareTerminal.objects.create(name="Bar", register=self.bar, device_code_id="dc-bar", code="X", status="PAIRED", device_id="device-bar")
        with mock.patch("payments.square.gather_terminal_checkout", return_value=started("c-term")):
            answer, row = self.start()
        self.assertNotIn("phone", answer)
        self.assertEqual(row.kind, "terminal")

    def test_the_store_card_form(self):
        page = self.client.get(reverse("web-payment-window"), {"amount": "12.50"})
        config = page.context["config"]
        self.assertEqual((config["amountText"], config["sandbox"], config["mode"]), ("$12.50", True, "sheet"))
        self.assertContains(page, "sandbox.web.squarecdn.com")
        self.assertNotContains(page, "UrkVan")
        self.assertNotContains(page, "&#x27;")  # the theme's font names arrive as written, not HTML-escaped
