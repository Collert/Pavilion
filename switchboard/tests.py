"""
Switchboard's protocol, from Pavilion's side: the manifest, connecting, the bearer API with idempotency, each action,
and events with their signature, retries and log. Outgoing HTTP is faked at one seam, `switchboard.events.post`;
`background` runs straight away and `wait` only records, so deliveries play out inside the test.
"""
import datetime
import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gift_cards.models import StaffCard, StaffCardType
from pos_server.models import OrderStation
from pos_server.orders import create_order
from pos_server.tests import MenuFixture
from team import clock
from team.models import Shift
from users.models import GroupInfo, Role, Staff

from . import connections, events
from .models import AuthorizeCode, Delivery, IdempotencyRecord, IssuedCard, SwitchboardConnection, hash_token

CALLBACK = "https://switchboard.example.org/connect/callback"
HOOK = "https://switchboard.example.org/hooks/building/abc"


def connect(name="Holy Eucharist", hook=HOOK, user=None):
    """A connected Switchboard: (connection, api key, webhook secret)."""
    code = connections.new_code(user, name, CALLBACK)
    return connections.redeem(code, hook, name, base_url="https://pavilion.example.org")


def member(username, perms, **fields):
    """A staff member whose one group grants `perms`."""
    user = User.objects.create_user(username, password="x", is_staff=True, **fields)
    group = Group.objects.create(name=f"{username}'s group")
    GroupInfo.objects.create(group=group, perms=perms)
    user.groups.add(group)
    return user


class Sender:
    """Stands in for events.post: answers each call with the next status (the last one from then on)."""

    def __init__(self, *statuses):
        self.statuses, self.calls = list(statuses) or [200], []

    def __call__(self, url, body, headers, timeout=None):
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        status = self.statuses[min(len(self.calls), len(self.statuses)) - 1]
        if isinstance(status, Exception):
            raise status
        return status


class Api:
    """Calls with a connection's key."""

    def call(self, name, body, key=None, idempotency=None, raw=None):
        headers = {"Authorization": f"Bearer {key or self.key}"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        return self.client.post(f"/switchboard/v1/actions/{name}", raw if raw is not None else json.dumps(body),
                                content_type="application/json", headers=headers)


class ManifestTests(TestCase):
    def test_manifest_is_public_json_built_from_the_host(self):
        response = self.client.get("/.well-known/switchboard", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        m = response.json()
        self.assertEqual((m["protocol"], m["building"], m["name"]), ("1", "pavilion", "Pavilion"))
        self.assertEqual(m["instance"], "My restaurant")
        self.assertEqual(m["authorize_url"], "https://testserver/switchboard/authorize")
        self.assertEqual(m["icon"], "https://testserver/static/pavilion/pavilion.svg")
        self.assertEqual(m["token_url"], "https://testserver/switchboard/v1/token")
        self.assertEqual(m["api_base"], "https://testserver/switchboard/v1")

    def test_everything_the_protocol_lists_for_pavilion_is_there(self):
        m = self.client.get("/.well-known/switchboard").json()
        triggers = {t["type"]: {f["key"] for f in t["fields"]} for t in m["triggers"]}
        self.assertEqual(set(triggers), {"order.placed", "order.ready", "staff.clocked_in", "staff.clocked_out"})
        self.assertLessEqual({"order.id", "order.number", "order.type", "order.total", "order.items", "customer.name", "customer.email"},
                             triggers["order.placed"])
        self.assertLessEqual({"staff.id", "staff.name", "staff.email", "at"}, triggers["staff.clocked_in"])
        self.assertIn("hours", triggers["staff.clocked_out"])
        actions = {a["name"]: a for a in m["actions"]}
        self.assertEqual(set(actions), {"staff.find", "staff.ensure", "staff_card.issue", "shift.add"})
        self.assertEqual(actions["staff.find"]["kind"], "lookup")
        expected = {"staff.find": ({"email"}, {"found", "staff_id", "name", "email", "roles"}),
                    "staff.ensure": ({"name", "email", "role"}, {"staff_id", "created"}),
                    "staff_card.issue": ({"staff_id", "amount", "label", "replace_previous"}, {"card_id", "amount", "label", "replaced"}),
                    "shift.add": ({"staff_id", "starts_at", "ends_at", "role"}, {"shift_id", "status"})}
        for name, (params, output) in expected.items():
            a = actions[name]
            self.assertEqual((a["method"], a["path"]), ("POST", f"/actions/{name}"))
            self.assertLessEqual(params, {p["key"] for p in a["params"]}, name)
            self.assertLessEqual(output, {f["key"] for f in a["output"]}, name)

    def test_every_field_is_well_formed(self):
        m = self.client.get("/.well-known/switchboard").json()
        types = {"string", "int", "number", "bool", "datetime", "money"}
        for t in m["triggers"]:
            self.assertTrue(t["title"] and t["description"] and t["summary"], t["type"])
            for f in t["fields"]:
                self.assertIn(f["type"], types)
                self.assertIn("example", f)
                self.assertTrue(f["label"])
        for a in m["actions"]:
            self.assertIn(a["kind"], ("lookup", "action"))
            for p in a["params"]:
                self.assertIn(p["type"], types)
                self.assertIsInstance(p["required"], bool)
            for f in a["output"]:
                self.assertIn(f["type"], types)
                self.assertIn("example", f)

    @override_settings(SETUP_REDIRECT=True, THROTTLE=True)
    def test_manifest_and_api_answer_json_before_first_time_setup(self):
        from django.core.cache import cache
        cache.clear()
        self.assertFalse(User.objects.exists())
        self.assertEqual(self.client.get("/.well-known/switchboard").json()["building"], "pavilion")
        response = self.client.post("/switchboard/v1/token", "{}", content_type="application/json")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/switchboard/v1/").json()["error"]["code"], "unauthorized")
        self.assertEqual(self.client.get("/switchboard/authorize").status_code, 302)  # pages still go to setup
        cache.clear()


class HandshakeTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "owner@example.org", "x", first_name="Olha", last_name="Owner")
        self.query = {"callback": CALLBACK, "state": "st-123", "name": "Holy Eucharist"}

    def test_signed_out_people_sign_in_and_come_back(self):
        response = self.client.get("/switchboard/authorize", self.query)
        self.assertEqual(response.status_code, 302)
        target = urlsplit(response["Location"])
        self.assertEqual(target.path, reverse("login_view"))
        back = parse_qs(target.query)["next"][0]
        self.assertTrue(back.startswith("/switchboard/authorize?"))
        self.assertEqual(parse_qs(urlsplit(back).query)["state"], ["st-123"])
        from django.test import RequestFactory
        from pos_server.devices import safe_next_url
        self.assertEqual(safe_next_url(RequestFactory().get("/"), back), back)  # the login page takes it

    def test_consent_page_names_the_host_and_what_it_allows(self):
        self.client.force_login(self.owner)
        response = self.client.get("/switchboard/authorize", self.query)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "switchboard.example.org")
        self.assertContains(response, "Holy Eucharist")
        self.assertContains(response, "Issue staff cards")
        self.assertContains(response, 'value="st-123"')

    def test_approve_gives_a_code_that_becomes_a_connection(self):
        self.client.force_login(self.owner)
        response = self.client.post("/switchboard/authorize", {**self.query, "op": "approve"})
        self.assertEqual(response.status_code, 302)
        back = urlsplit(response["Location"])
        self.assertEqual(f"{back.scheme}://{back.netloc}{back.path}", CALLBACK)
        answer = parse_qs(back.query)
        self.assertEqual(answer["state"], ["st-123"])
        code = answer["code"][0]
        self.assertFalse(AuthorizeCode.objects.filter(code_hash=code).exists())  # only its hash is kept
        self.client.logout()

        response = self.client.post("/switchboard/v1/token", json.dumps({"code": code, "webhook_url": HOOK, "name": "Holy Eucharist"}),
                                    content_type="application/json", secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"api_key", "webhook_secret", "connection_id", "building", "instance"})
        self.assertTrue(body["api_key"].startswith("sbk_"))
        self.assertEqual((body["building"], body["instance"]), ("pavilion", "My restaurant"))
        connection = SwitchboardConnection.objects.get()
        self.assertEqual(body["connection_id"], str(connection.pk))
        self.assertEqual((connection.name, connection.webhook_url, connection.connected_by), ("Holy Eucharist", HOOK, self.owner))
        self.assertEqual(connection.base_url, "https://testserver")
        self.assertEqual(connection.key_hash, hashlib.sha256(body["api_key"].encode()).hexdigest())
        self.assertNotIn(body["api_key"], json.dumps(list(SwitchboardConnection.objects.values()), default=str))
        self.assertNotIn(body["webhook_secret"], connection.secret)  # encrypted, but readable to sign with
        from payments import secrets
        self.assertEqual(secrets.decrypt(connection.secret), body["webhook_secret"])

        ping = self.client.get("/switchboard/v1/", headers={"Authorization": f"Bearer {body['api_key']}"})
        self.assertEqual(ping.json(), {"ok": True, "building": "pavilion", "instance": "My restaurant", "connection_id": str(connection.pk)})

        again = self.client.post("/switchboard/v1/token", json.dumps({"code": code, "webhook_url": HOOK}), content_type="application/json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json()["error"]["code"], "invalid_code")
        self.assertEqual(SwitchboardConnection.objects.count(), 1)

    def test_cancel_goes_back_with_access_denied(self):
        self.client.force_login(self.owner)
        response = self.client.post("/switchboard/authorize", {**self.query, "callback": CALLBACK + "?from=sb", "op": "cancel"})
        answer = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(answer, {"from": ["sb"], "error": ["access_denied"], "state": ["st-123"]})
        self.assertFalse(AuthorizeCode.objects.exists())

    def test_wrong_and_expired_codes(self):
        def token(code):
            return self.client.post("/switchboard/v1/token", json.dumps({"code": code, "webhook_url": HOOK}), content_type="application/json")
        for bad in ("nope", "", None):
            response = token(bad)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid_code"))
        code = connections.new_code(self.owner, "Late", CALLBACK)
        AuthorizeCode.objects.update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(token(code).json()["error"]["code"], "invalid_code")
        self.assertFalse(SwitchboardConnection.objects.exists())

    def test_a_code_lasts_ten_minutes(self):
        connections.new_code(self.owner, "Soon", CALLBACK)
        code = AuthorizeCode.objects.get()
        self.assertEqual(code.expires_at - code.created_at, datetime.timedelta(minutes=10))

    def test_token_needs_a_json_object_and_a_web_address(self):
        code = connections.new_code(self.owner, "X", CALLBACK)
        response = self.client.post("/switchboard/v1/token", "[1]", content_type="application/json")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid_request"))
        response = self.client.post("/switchboard/v1/token", json.dumps({"code": code, "webhook_url": "ftp://x"}), content_type="application/json")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "invalid_params"))
        self.assertTrue(AuthorizeCode.objects.filter(used_at__isnull=True).exists())  # the code wasn't spent on a bad call

    def test_callback_must_be_a_web_address(self):
        self.client.force_login(self.owner)
        for callback in ("javascript:alert(1)", "", "/relative"):
            self.assertEqual(self.client.get("/switchboard/authorize", {**self.query, "callback": callback}).status_code, 400)
            self.assertEqual(self.client.post("/switchboard/authorize", {**self.query, "callback": callback, "op": "approve"}).status_code, 400)
        self.assertFalse(AuthorizeCode.objects.exists())

    def test_only_people_who_can_edit_the_switchboard_page_approve(self):
        viewer = member("viewer", ["app.admin", "admin.switchboard.view", "admin.devices.edit"])
        self.client.force_login(viewer)
        self.assertEqual(self.client.get("/switchboard/authorize", self.query).status_code, 403)
        self.assertEqual(self.client.post("/switchboard/authorize", {**self.query, "op": "approve"}).status_code, 403)
        self.assertFalse(AuthorizeCode.objects.exists())
        editor = member("editor", ["app.admin", "admin.switchboard.view", "admin.switchboard.edit"])
        self.client.force_login(editor)
        self.assertEqual(self.client.get("/switchboard/authorize", self.query).status_code, 200)
        customer = User.objects.create_user("customer", password="x")
        self.client.force_login(customer)
        self.assertEqual(self.client.get("/switchboard/authorize", self.query).status_code, 302)  # to the staff sign-in
        self.assertFalse(AuthorizeCode.objects.exists())


class ApiTests(Api, TestCase):
    def setUp(self):
        self.connection, self.key, self.secret = connect()
        self.dana = User.objects.create_user("dana", "Dana@Example.org", "x", is_staff=True, first_name="Dana", last_name="Lee")
        self.barista = Role.objects.get_or_create(name="Barista")[0]
        Staff.objects.create(user=self.dana).roles.add(self.barista)

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])

    # ----- auth and errors -----

    def test_missing_wrong_and_revoked_keys(self):
        for headers in ({}, {"Authorization": "Bearer sbk_wrong"}, {"Authorization": self.key}, {"Authorization": "Basic abc"}):
            self.assertError(self.client.get("/switchboard/v1/", headers=headers), 401, "unauthorized")
            self.assertError(self.client.post("/switchboard/v1/actions/staff.find", "{}", content_type="application/json", headers=headers),
                             401, "unauthorized")
        self.assertEqual(self.client.get("/switchboard/v1", headers={"Authorization": f"Bearer {self.key}"}).status_code, 200)

    def test_api_calls_need_no_csrf_token_or_session(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        response = client.post("/switchboard/v1/actions/staff.find", json.dumps({"email": "dana@example.org"}),
                               content_type="application/json", headers={"Authorization": f"Bearer {self.key}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.post("/switchboard/v1/token", "{}", content_type="application/json").status_code, 422)

    def test_unknown_action_bad_body_and_wrong_method(self):
        self.assertError(self.call("staff.promote", {}), 404, "unknown_action")
        self.assertError(self.client.get("/switchboard/v1/nothing/here", headers={"Authorization": f"Bearer {self.key}"}), 404, "unknown_action")
        self.assertError(self.call("staff.find", None, raw="[1, 2]"), 400, "invalid_request")
        self.assertError(self.call("staff.find", None, raw="{not json"), 400, "invalid_request")
        self.assertError(self.client.get("/switchboard/v1/actions/staff.find", headers={"Authorization": f"Bearer {self.key}"}), 405, "invalid_request")
        self.assertError(self.call("shift.add", {}, idempotency="k" * 201), 400, "invalid_request")

    def test_something_unexpected_is_a_json_500_and_is_not_remembered(self):
        with mock.patch.dict("switchboard.actions.RUN", {"staff.ensure": mock.Mock(side_effect=RuntimeError("boom"))}), \
                self.assertLogs("switchboard.views", "ERROR"):
            self.assertError(self.call("staff.ensure", {"name": "A B", "email": "a@example.org"}, idempotency="k1"), 500, "server_error")
        self.assertFalse(IdempotencyRecord.objects.exists())
        self.assertEqual(self.call("staff.ensure", {"name": "A B", "email": "a@example.org"}, idempotency="k1").status_code, 200)

    @override_settings(THROTTLE=True)
    def test_a_flood_is_asked_to_come_back_in_the_protocols_shape(self):
        from django.core.cache import cache
        cache.clear()
        with mock.patch("switchboard.views.CALLS", 2):
            for _n in range(2):
                self.assertEqual(self.client.get("/switchboard/v1/", headers={"Authorization": f"Bearer {self.key}"}).status_code, 200)
            response = self.client.get("/switchboard/v1/", headers={"Authorization": f"Bearer {self.key}"})
        self.assertError(response, 503, "server_error")
        self.assertTrue(response["Retry-After"])
        cache.clear()

    def test_switchboard_disconnects(self):
        response = self.client.delete("/switchboard/v1/connection", headers={"Authorization": f"Bearer {self.key}"})
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        self.assertFalse(SwitchboardConnection.objects.exists())
        self.assertError(self.client.get("/switchboard/v1/", headers={"Authorization": f"Bearer {self.key}"}), 401, "unauthorized")
        sender = Sender()
        with mock.patch("switchboard.events.post", sender), mock.patch("switchboard.events.background", lambda job: job()), \
                self.captureOnCommitCallbacks(execute=True):
            clock.clock_in(self.dana, approver=self.dana)
        self.assertEqual(sender.calls, [])  # events stop

    # ----- staff.find -----

    def test_find_staff_by_email_whatever_the_case(self):
        response = self.call("staff.find", {"email": " DANA@example.ORG "})
        self.assertEqual(response.json(), {"found": True, "staff_id": str(self.dana.pk), "name": "Dana Lee", "email": "Dana@example.org",
                                           "roles": "Barista", "active": True})

    def test_finding_nobody_is_not_an_error(self):
        User.objects.create_user("customer", "customer@example.org", "x")  # an account, but not staff
        for email in ("nobody@example.org", "customer@example.org"):
            response = self.call("staff.find", {"email": email})
            self.assertEqual((response.status_code, response.json()), (200, {"found": False}))
        self.assertError(self.call("staff.find", {}), 422, "invalid_params")
        self.assertIn("Email", self.call("staff.find", {"email": ""}).json()["error"]["message"])

    # ----- staff.ensure -----

    def test_ensure_returns_the_staff_member_there_is(self):
        response = self.call("staff.ensure", {"name": "Someone Else", "email": "dana@EXAMPLE.org"}, idempotency="e1")
        self.assertEqual(response.json(), {"staff_id": str(self.dana.pk), "name": "Dana Lee", "email": "Dana@example.org", "created": False})
        self.assertEqual(User.objects.count(), 1)

    def test_ensure_makes_a_staff_account_with_a_role_and_its_starting_groups(self):
        crew = Group.objects.create(name="Test crew")
        info = GroupInfo.objects.filter(owners=True).first() or GroupInfo.objects.create(group=Group.objects.create(name="Test owners"), owners=True)
        owners = info.group
        cook = Role.objects.get_or_create(name="Line cook")[0]
        cook.groups.set([crew, owners])
        with mock.patch("misc_tools.email.send") as send, self.captureOnCommitCallbacks(execute=True):
            response = self.call("staff.ensure", {"name": "Maria Olena Kovalenko", "email": "maria@example.org", "role": "line COOK"}, idempotency="e2")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["created"])
        maria = User.objects.get(pk=int(body["staff_id"]))
        self.assertEqual((maria.first_name, maria.last_name, maria.email, maria.username), ("Maria", "Olena Kovalenko", "maria@example.org", "maria@example.org"))
        self.assertTrue(maria.is_staff and maria.is_active)
        self.assertFalse(maria.has_usable_password())
        self.assertFalse(maria.is_superuser)
        self.assertEqual(list(maria.staff_profile.roles.all()), [cook])
        self.assertEqual(list(maria.groups.all()), [crew])  # the role's starting groups, never Owners
        send.assert_not_called()  # no invitation unless asked
        found = self.call("staff.find", {"email": "maria@example.org"}).json()
        self.assertEqual((found["staff_id"], found["roles"]), (body["staff_id"], "Line cook"))

    def test_ensure_can_email_the_invitation(self):
        with mock.patch("misc_tools.email.send") as send, self.captureOnCommitCallbacks(execute=True):
            self.call("staff.ensure", {"name": "Ivan P", "email": "ivan@example.org", "invite": True}, idempotency="e3")
        self.assertEqual(send.call_args.args[:2], ("staff_invite", "ivan@example.org"))
        self.assertIn("/users/welcome/", send.call_args.args[2]["link"])

    def test_ensure_turns_a_customers_account_into_staff(self):
        customer = User.objects.create_user("ana", "ana@example.org", "pw-kept", first_name="Ana")
        body = self.call("staff.ensure", {"name": "Ana B", "email": "ANA@example.org"}, idempotency="e4").json()
        customer.refresh_from_db()
        self.assertEqual((body["staff_id"], body["created"], body["name"]), (str(customer.pk), True, "Ana"))
        self.assertTrue(customer.is_staff and customer.check_password("pw-kept"))
        self.assertTrue(Staff.objects.filter(user=customer).exists())

    def test_ensure_never_touches_owners(self):
        owner = User.objects.create_superuser("boss", "boss@example.org", "x", first_name="Boss")
        Staff.objects.create(user=owner)
        body = self.call("staff.ensure", {"name": "X Y", "email": "boss@example.org", "role": "Barista"}, idempotency="e5").json()
        self.assertEqual((body["staff_id"], body["created"]), (str(owner.pk), False))
        self.assertFalse(owner.staff_profile.roles.exists())
        self.assertFalse(owner.groups.exists())

    def test_ensure_says_which_param_is_wrong(self):
        self.assertIn("Name", self.assertErrorMessage(self.call("staff.ensure", {"email": "a@example.org"}, idempotency="e6"), 422))
        self.assertIn("Email", self.assertErrorMessage(self.call("staff.ensure", {"name": "A", "email": "not-an-email"}, idempotency="e7"), 422))
        message = self.assertErrorMessage(self.call("staff.ensure", {"name": "A", "email": "a@example.org", "role": "Pilot"}, idempotency="e8"), 422)
        self.assertIn("Pilot", message)
        self.assertIn("Barista", message)
        self.assertFalse(User.objects.filter(email="a@example.org").exists())

    def assertErrorMessage(self, response, status):
        self.assertEqual(response.status_code, status, response.content)
        return response.json()["error"]["message"]

    def test_a_repeated_key_gets_the_same_answer_without_doing_it_again(self):
        first = self.call("staff.ensure", {"name": "Maria K", "email": "maria@example.org"}, idempotency="level-3")
        maria = User.objects.get(email="maria@example.org")
        maria.first_name = "Renamed"
        maria.save()
        with mock.patch.dict("switchboard.actions.RUN", {"staff.ensure": mock.Mock(side_effect=AssertionError("ran again"))}):
            second = self.call("staff.ensure", {"name": "Maria K", "email": "maria@example.org"}, idempotency="level-3")
        self.assertEqual((second.status_code, second.json()), (first.status_code, first.json()))
        self.assertTrue(first.json()["created"])
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        # errors are remembered too, and keys are each connection's own
        bad = self.call("staff.ensure", {"email": "x@example.org"}, idempotency="bad-1")
        fixed = self.call("staff.ensure", {"name": "X", "email": "x@example.org"}, idempotency="bad-1")
        self.assertEqual((fixed.status_code, fixed.json()), (422, bad.json()))
        _other, other_key, _secret = connect("Another parish", "https://other.example.org/hooks/x")
        other = self.call("staff.ensure", {"name": "Maria K", "email": "maria@example.org"}, key=other_key, idempotency="level-3")
        self.assertEqual(other.json()["name"], "Renamed K")

    # ----- staff_card.issue -----

    def issue(self, key, **params):
        return self.call("staff_card.issue", {"staff_id": str(self.dana.pk), **params}, idempotency=key)

    def test_issue_a_card_worth_the_amount(self):
        response = self.issue("c1", amount=15, label="Volunteer meal")
        self.assertEqual(response.status_code, 200, response.content)
        card = StaffCard.objects.get()
        self.assertEqual(response.json(), {"card_id": str(card.pk), "amount": 15.0, "label": "Volunteer meal", "replaced": None})
        self.assertEqual((card.user, card.balance, card.revoked_at), (self.dana, Decimal("15.00"), None))
        t = card.type  # a one-time balance type the manager sees on the Staff cards page
        self.assertEqual((t.name, t.kind, t.refill, t.live, t.archived), ("Volunteer meal", StaffCardType.BALANCE, "never", True, False))
        self.assertEqual(set(t.channels), {"store", "pickup", "delivery"})
        self.assertEqual(IssuedCard.objects.get().connection, self.connection)
        from gift_cards import staff_cards
        self.assertEqual(staff_cards.problem(card, "store"), "")  # it pays right away
        self.assertEqual(staff_cards.refill(card, timezone.now() + datetime.timedelta(days=40)).balance, Decimal("15.00"))  # and never refills

    def test_the_same_idempotency_key_issues_one_card(self):
        first = self.issue("level-3-dana", amount="10.50")
        second = self.issue("level-3-dana", amount="10.50")
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["label"], "Switchboard")
        self.assertEqual(StaffCard.objects.count(), 1)
        self.assertEqual(StaffCard.objects.get().balance, Decimal("10.50"))

    def test_replace_previous_retires_the_card_this_connection_issued(self):
        manual = StaffCardType.objects.create(name="Manager comp", kind=StaffCardType.BALANCE)
        kept = StaffCard.objects.create(type=manual, user=self.dana, number=StaffCard.new_number(), balance=5)  # a manager's own
        old = self.issue("c1", amount=10, label="Level 2 meal").json()
        new = self.issue("c2", amount=20, label="Level 3 meal", replace_previous=True).json()
        self.assertEqual(new["replaced"], old["card_id"])
        self.assertIsNotNone(StaffCard.objects.get(pk=old["card_id"]).revoked_at)
        live = StaffCard.objects.filter(user=self.dana, revoked_at__isnull=True)
        self.assertEqual({c.pk for c in live}, {kept.pk, int(new["card_id"])})
        # the same label again swaps the card instead of stacking a second one
        again = self.issue("c3", amount=25, label="Level 3 meal", replace_previous=True).json()
        self.assertEqual(again["replaced"], new["card_id"])
        self.assertEqual(live.filter(type__name="Level 3 meal").get().balance, Decimal("25.00"))
        self.assertEqual(StaffCardType.objects.filter(name="Level 3 meal").count(), 1)

    def test_a_reconnected_switchboard_still_replaces_its_cards(self):
        old = self.issue("c1", amount=10).json()
        connections.disconnect(self.connection)
        self.assertTrue(StaffCard.objects.filter(pk=old["card_id"], revoked_at__isnull=True).exists())  # cards stay
        _connection, self.key, _secret = connect()
        new = self.issue("c1", amount=12, replace_previous=True).json()
        self.assertEqual(new["replaced"], old["card_id"])

    def test_without_replace_cards_stack_but_never_two_of_one_type(self):
        self.issue("c1", amount=10, label="Level 2 meal")
        self.assertEqual(self.issue("c2", amount=20, label="Level 3 meal").status_code, 200)
        self.assertEqual(StaffCard.objects.filter(user=self.dana, revoked_at__isnull=True).count(), 2)
        clash = self.issue("c3", amount=30, label="level 3 MEAL")
        self.assertError(clash, 409, "conflict")
        self.assertEqual(StaffCard.objects.count(), 2)
        self.assertEqual(self.issue("c3", amount=30, label="Level 3 meal").json(), clash.json())  # and that answer is replayed

    def test_a_label_that_is_one_of_the_stores_types_uses_it(self):
        meal = StaffCardType.objects.create(name="Shift meal", kind=StaffCardType.BALANCE, amount=Decimal("12.00"), refill="day", channels=["store"])
        body = self.issue("c1", amount=30, label="shift meal").json()
        card = StaffCard.objects.get(pk=body["card_id"])
        self.assertEqual((card.type, body["label"], card.balance), (meal, "Shift meal", Decimal("30.00")))
        from gift_cards import staff_cards
        self.assertEqual(staff_cards.refill(card, timezone.now() + datetime.timedelta(days=2)).balance, Decimal("12.00"))  # then its own rules
        StaffCardType.objects.create(name="Staff discount", kind=StaffCardType.DISCOUNT)
        self.assertError(self.issue("c2", amount=5, label="Staff discount"), 409, "conflict")

    def test_issue_refuses_bad_params_and_people_who_cant_use_a_card(self):
        self.assertError(self.call("staff_card.issue", {"amount": 5}, idempotency="b1"), 422, "invalid_params")
        self.assertError(self.call("staff_card.issue", {"staff_id": "99999", "amount": 5}, idempotency="b2"), 422, "invalid_params")
        self.assertError(self.issue("b3"), 422, "invalid_params")
        for n, amount in enumerate(("0", -4, "ten", True, 100000)):
            self.assertError(self.issue(f"b4-{n}", amount=amount), 422, "invalid_params")
        self.assertError(self.issue("b5", amount=5, label="x" * 31), 422, "invalid_params")
        self.assertError(self.issue("b6", amount=5, replace_previous="maybe"), 422, "invalid_params")
        customer = User.objects.create_user("customer", "c@example.org", "x")
        self.assertError(self.call("staff_card.issue", {"staff_id": str(customer.pk), "amount": 5}, idempotency="b7"), 422, "invalid_params")
        self.dana.is_active = False
        self.dana.save()
        self.assertError(self.issue("b8", amount=5), 409, "conflict")
        self.assertFalse(StaffCard.objects.exists())
        self.assertFalse(StaffCardType.objects.exists())  # a refused call leaves no card type behind

    # ----- shift.add -----

    @staticmethod
    def utc(hour, minute=0) -> str:
        """That time on Dec 5 at the store, written in UTC."""
        return timezone.make_aware(datetime.datetime(2026, 12, 5, hour, minute)).astimezone(datetime.UTC).isoformat()

    def add_shift(self, key, starts, ends, **params):
        return self.call("shift.add", {"staff_id": str(self.dana.pk), "starts_at": starts, "ends_at": ends, **params}, idempotency=key)

    def test_add_a_published_shift_in_the_stores_time(self):
        with mock.patch("misc_tools.email.send") as send, self.captureOnCommitCallbacks(execute=True):
            # sent in UTC, as another building in another zone would: 09:00-13:30 at the store
            response = self.add_shift("s1", self.utc(9), self.utc(13, 30).replace("+00:00", "Z"), note="Parish festival")
        self.assertEqual(response.status_code, 200, response.content)
        shift = Shift.objects.get()
        self.assertEqual(response.json(), {"shift_id": str(shift.pk), "status": "published", "created": True})
        self.assertEqual((shift.user, shift.role, shift.date, shift.start, shift.end), (self.dana, self.barista, datetime.date(2026, 12, 5), 540, 810))
        self.assertTrue(shift.published and not shift.deleted)
        self.assertEqual((shift.note, shift.published_as["start"]), ("Parish festival", 540))
        self.assertEqual(send.call_args.args[:2], ("schedule_changed", "Dana@example.org"))  # told like Publish tells them
        # the time clock takes it: no manager needed inside the shift
        at = timezone.make_aware(datetime.datetime(2026, 12, 5, 9, 5))
        self.assertEqual(clock.check_in(self.dana, at)["shift"], shift)
        self.assertTrue(clock.check_in(self.dana, at)["ok"])

    def test_an_identical_shift_is_returned_not_added_twice(self):
        first = self.add_shift("s1", "2026-12-05T09:00:00", "2026-12-05T13:00:00").json()
        again = self.add_shift("s2", "2026-12-05T09:00:00", "2026-12-05T13:00:00").json()
        self.assertEqual(again, {"shift_id": first["shift_id"], "status": "published", "created": False})
        Shift.objects.update(published=False)  # a manager is editing it
        self.assertEqual(self.add_shift("s3", self.utc(9), self.utc(13)).json()["status"], "unpublished")
        self.assertEqual(Shift.objects.count(), 1)

    def test_shift_roles(self):
        cook = Role.objects.get_or_create(name="Line cook")[0]
        body = self.add_shift("s1", "2026-12-05T09:00:00-08:00", "2026-12-05T13:00:00-08:00", role="line cook").json()
        self.assertEqual(Shift.objects.get(pk=body["shift_id"]).role, cook)
        self.assertError(self.add_shift("s2", "2026-12-06T09:00:00-08:00", "2026-12-06T13:00:00-08:00", role="Pilot"), 422, "invalid_params")
        self.dana.staff_profile.roles.clear()
        body = self.add_shift("s3", "2026-12-07T09:00:00-08:00", "2026-12-07T13:00:00-08:00").json()
        self.assertIsNone(Shift.objects.get(pk=body["shift_id"]).role)

    def test_shift_times_that_cant_be(self):
        self.assertError(self.add_shift("t1", "2026-12-05T13:00:00-08:00", "2026-12-05T09:00:00-08:00"), 422, "invalid_params")
        self.assertError(self.add_shift("t2", "next tuesday", "2026-12-05T09:00:00-08:00"), 422, "invalid_params")
        self.assertError(self.add_shift("t3", "2026-12-05T22:00:00-08:00", "2026-12-06T02:00:00-08:00"), 422, "invalid_params")
        self.assertError(self.call("shift.add", {"staff_id": "0", "starts_at": "2026-12-05T09:00:00-08:00", "ends_at": "2026-12-05T10:00:00-08:00"},
                                   idempotency="t4"), 422, "invalid_params")
        self.assertError(self.add_shift("t5", "2026-12-05T09:00:00-08:00", "2026-12-05T10:00:00-08:00", note="n" * 81), 422, "invalid_params")
        self.assertFalse(Shift.objects.exists())
        # up to midnight is fine, and a time without an offset is the store's own
        body = self.add_shift("t6", "2026-12-05T20:00:00", "2026-12-06T00:00:00").json()
        shift = Shift.objects.get(pk=body["shift_id"])
        self.assertEqual((shift.date, shift.start, shift.end), (datetime.date(2026, 12, 5), 1200, 1440))

    def test_an_overlapping_shift_is_a_conflict(self):
        self.add_shift("o1", "2026-12-05T09:00:00-08:00", "2026-12-05T13:00:00-08:00")
        clash = self.add_shift("o2", "2026-12-05T12:00:00-08:00", "2026-12-05T16:00:00-08:00")
        self.assertError(clash, 409, "conflict")
        self.assertIn("overlaps", clash.json()["error"]["message"])
        self.assertEqual(self.add_shift("o3", "2026-12-05T13:00:00-08:00", "2026-12-05T16:00:00-08:00").status_code, 200)  # back to back is fine
        self.assertEqual(Shift.objects.count(), 2)


@mock.patch("switchboard.events.background", lambda job: job())
class EventTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.dana = User.objects.create_user("dana", "dana@example.org", "x", is_staff=True, first_name="Dana", last_name="Lee")
        role = Role.objects.get_or_create(name="Barista")[0]
        Staff.objects.create(user=cls.dana).roles.add(role)
        cls.boss = User.objects.create_superuser("boss", "boss@example.org", "x")

    def setUp(self):
        self.connection, self.key, self.secret = connect()
        self.waits = []
        patcher = mock.patch("switchboard.events.wait", self.waits.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self, sender, n=0):
        call = sender.calls[n]
        return call, json.loads(call["body"])

    def place(self, **fields):
        fields = {"name": "Maria Kovalenko", "email": "maria@example.org", **fields}
        return create_order(lines=[self.dish(self.tea, 2), self.dish(self.burger, cust={"groups": {self.temp_key: [self.medium.id], self.bun_key: []}})],
                            channel="web", initial_status=OrderStation.STATUS_PENDING, **fields)

    def tea_order(self, channel="web", status=OrderStation.STATUS_PENDING, **fields):
        return create_order(lines=[self.dish(self.tea, 2)], channel=channel, initial_status=status, **fields)

    def test_order_placed_is_posted_signed_after_the_commit(self):
        sender = Sender(200)
        with mock.patch("switchboard.events.post", sender):
            with self.captureOnCommitCallbacks(execute=True):
                order = self.tea_order(name="Maria Kovalenko", email="maria@example.org")
                self.assertEqual(sender.calls, [])  # nothing leaves before the order is saved for good
                self.assertFalse(Delivery.objects.exists())
        call, event = self.sent(sender)
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual((call["url"], call["timeout"]), (HOOK, 10))
        headers = call["headers"]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["User-Agent"], "Pavilion/1.0 (Switchboard)")
        self.assertEqual(headers["X-Switchboard-Event"], "order.placed")
        self.assertEqual(headers["X-Switchboard-Delivery"], event["id"])
        expected = "sha256=" + hmac.new(self.secret.encode(), call["body"], hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-Switchboard-Signature"], expected)
        self.assertRegex(event["id"], r"^evt_[0-9a-f]{20}$")
        self.assertEqual(set(event), {"id", "type", "source", "occurred_at", "subject", "data"})
        self.assertEqual(event["type"], "order.placed")
        self.assertEqual(event["source"], {"building": "pavilion", "instance": "https://pavilion.example.org"})
        self.assertEqual(datetime.datetime.fromisoformat(event["occurred_at"]), order.timestamp.replace(microsecond=0))
        self.assertIsNotNone(datetime.datetime.fromisoformat(event["occurred_at"]).utcoffset())
        self.assertEqual(event["subject"], {"type": "order", "id": str(order.pk), "email": "maria@example.org", "name": "Maria Kovalenko"})
        self.assertEqual(event["data"], {
            "order": {"id": str(order.pk), "number": f"{order.pk:04d}", "type": "pickup", "channel": "web", "total": float(order.total), "items": 2},
            "customer": {"name": "Maria Kovalenko", "email": "maria@example.org"}})
        delivery = Delivery.objects.get()
        self.assertEqual((delivery.status, delivery.attempts, delivery.response_status, delivery.event_type, delivery.body),
                         (Delivery.DELIVERED, 1, 200, "order.placed", ""))
        self.assertIsNotNone(SwitchboardConnection.objects.get().last_delivery_at)
        self.assertEqual(self.waits, [])

    def test_every_channel_emits_and_the_manifests_fields_are_in_the_event(self):
        sender = Sender(200)
        customer = User.objects.create_user("ana", "ana@example.org", "x", first_name="Ana", last_name="B")
        with mock.patch("switchboard.events.post", sender), self.captureOnCommitCallbacks(execute=True):
            self.tea_order("delivery", customer=customer)
            create_order(lines=[self.dish(self.tea)], channel="store", initial_status=OrderStation.STATUS_APPROVED, to_go_order=True)
            create_order(lines=[self.dish(self.tea)], channel="store", initial_status=OrderStation.STATUS_APPROVED)
        kinds = [json.loads(c["body"])["data"]["order"]["type"] for c in sender.calls]
        self.assertEqual(kinds, ["delivery", "to_go", "dine_in"])
        first = json.loads(sender.calls[0]["body"])
        self.assertEqual(first["data"]["customer"], {"name": "Ana B", "email": "ana@example.org"})  # from their account
        manifest = self.client.get("/.well-known/switchboard").json()
        for trigger in manifest["triggers"]:
            if trigger["type"] == "order.placed":
                for f in trigger["fields"]:
                    node = first["data"]
                    for part in f["key"].split("."):
                        node = node[part]

    def test_order_ready_when_the_last_station_finishes(self):
        sender = Sender(200)
        with mock.patch("switchboard.events.post", sender):
            with self.captureOnCommitCallbacks(execute=True):
                order = self.place()
            with self.captureOnCommitCallbacks(execute=True):
                order.set_station_status(["kitchen", "bar"], OrderStation.STATUS_APPROVED)
                order.set_station_status(["bar"], OrderStation.STATUS_DONE)
            self.assertEqual([c["headers"]["X-Switchboard-Event"] for c in sender.calls], ["order.placed"])  # the kitchen is still on it
            with self.captureOnCommitCallbacks(execute=True):
                order.set_station_status(["kitchen"], OrderStation.STATUS_DONE)
        self.assertEqual([c["headers"]["X-Switchboard-Event"] for c in sender.calls], ["order.placed", "order.ready"])
        _call, event = self.sent(sender, 1)
        self.assertEqual(event["data"]["order"]["id"], str(order.pk))
        self.assertEqual(event["data"]["order"]["items"], 3)
        self.assertEqual(event["data"]["customer"]["email"], "maria@example.org")
        self.assertNotEqual(event["id"], self.sent(sender, 0)[1]["id"])

    def test_clocking_in_and_out(self):
        sender = Sender(200)
        start = timezone.make_aware(datetime.datetime(2026, 12, 5, 9, 0))
        with mock.patch("switchboard.events.post", sender), self.captureOnCommitCallbacks(execute=True):
            clock.clock_in(self.dana, start, approver=self.boss)
            clock.start_break(self.dana, start + datetime.timedelta(hours=2))
            clock.end_break(self.dana, start + datetime.timedelta(hours=2, minutes=30))
            clock.clock_out(self.dana, start + datetime.timedelta(hours=5, minutes=45))
        self.assertEqual([c["headers"]["X-Switchboard-Event"] for c in sender.calls], ["staff.clocked_in", "staff.clocked_out"])
        _call, came = self.sent(sender, 0)
        staff = {"id": str(self.dana.pk), "name": "Dana Lee", "email": "dana@example.org"}
        self.assertEqual(came["subject"], {"type": "person", **staff})
        self.assertEqual(came["data"], {"staff": staff, "at": start.isoformat(), "role": "Barista"})
        self.assertEqual(came["occurred_at"], start.isoformat())
        self.assertRegex(came["data"]["at"], r"^2026-12-05T09:00:00[+-]\d\d:00$")  # the store's time, with its offset
        _call, left = self.sent(sender, 1)
        self.assertEqual(left["data"], {"staff": staff, "at": (start + datetime.timedelta(hours=5, minutes=45)).isoformat(), "role": "Barista", "hours": 5.25})

    def test_nothing_is_sent_or_logged_without_a_connection(self):
        SwitchboardConnection.objects.all().delete()
        sender = Sender(200)
        build = mock.Mock()
        with mock.patch("switchboard.events.post", sender), mock.patch("switchboard.events.background") as background, \
                self.captureOnCommitCallbacks(execute=True):
            self.tea_order()
            events.emit("order.placed", build)
        self.assertEqual(sender.calls, [])
        build.assert_not_called()
        background.assert_not_called()
        self.assertFalse(Delivery.objects.exists())

    def test_every_connection_gets_the_same_event_id_with_its_own_signature(self):
        _other, _key, other_secret = connect("Another parish", "https://other.example.org/hooks/x")
        sender = Sender(200)
        with mock.patch("switchboard.events.post", sender), self.captureOnCommitCallbacks(execute=True):
            self.tea_order()
        self.assertEqual({c["url"] for c in sender.calls}, {HOOK, "https://other.example.org/hooks/x"})
        self.assertEqual(len({json.loads(c["body"])["id"] for c in sender.calls}), 1)
        for call in sender.calls:
            secret = other_secret if "other" in call["url"] else self.secret
            self.assertEqual(call["headers"]["X-Switchboard-Signature"], "sha256=" + hmac.new(secret.encode(), call["body"], hashlib.sha256).hexdigest())

    def test_failed_deliveries_are_retried_with_growing_waits_and_the_same_body(self):
        sender = Sender(500, ConnectionError("down"), 503, 200)
        with mock.patch("switchboard.events.post", sender), self.captureOnCommitCallbacks(execute=True):
            self.tea_order()
        self.assertEqual(len(sender.calls), 4)
        self.assertEqual(self.waits, [5, 30, 120])
        self.assertEqual(len({c["body"] for c in sender.calls}), 1)
        self.assertEqual(len({c["headers"]["X-Switchboard-Delivery"] for c in sender.calls}), 1)
        delivery = Delivery.objects.get()
        self.assertEqual((delivery.status, delivery.attempts, delivery.response_status), (Delivery.DELIVERED, 4, 200))

    def test_five_tries_then_it_is_logged_as_failed(self):
        sender = Sender(500)
        with mock.patch("switchboard.events.post", sender), self.assertLogs("switchboard.events", "WARNING"), \
                self.captureOnCommitCallbacks(execute=True):
            order = self.tea_order()
        self.assertEqual(len(sender.calls), 5)
        self.assertEqual(self.waits, [5, 30, 120, 600])
        delivery = Delivery.objects.get()
        self.assertEqual((delivery.status, delivery.attempts, delivery.response_status, delivery.body), (Delivery.FAILED, 5, 500, ""))
        self.assertIsNone(SwitchboardConnection.objects.get().broken_at)
        self.assertTrue(type(order).objects.filter(pk=order.pk).exists())

    def test_a_410_stops_and_marks_the_connection_broken(self):
        sender = Sender(410)
        with mock.patch("switchboard.events.post", sender), self.assertLogs("switchboard.events", "WARNING"), \
                self.captureOnCommitCallbacks(execute=True):
            self.tea_order()
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(self.waits, [])
        connection = SwitchboardConnection.objects.get()  # kept, for the admin to see and disconnect
        self.assertTrue(connection.broken)
        self.assertEqual(Delivery.objects.get().status, Delivery.GONE)
        with mock.patch("switchboard.events.post", sender), self.captureOnCommitCallbacks(execute=True):
            self.tea_order()
        self.assertEqual(len(sender.calls), 1)  # nothing more goes to it
        self.assertEqual(self.client.get("/switchboard/v1/", headers={"Authorization": f"Bearer {self.key}"}).status_code, 200)

    def test_a_broken_sender_never_breaks_the_sale(self):
        with mock.patch("switchboard.events.post", side_effect=RuntimeError("boom")), self.assertLogs("switchboard.events", "WARNING"), \
                self.captureOnCommitCallbacks(execute=True):
            order = self.tea_order()
        self.assertEqual(Delivery.objects.get().status, Delivery.FAILED)
        self.assertIn("boom", Delivery.objects.get().error)
        with mock.patch("switchboard.events._order", side_effect=RuntimeError("bad build")), self.assertLogs("switchboard.events", "ERROR"), \
                self.captureOnCommitCallbacks(execute=True):
            second = self.tea_order()
        self.assertTrue(order.pk and second.pk)
        self.assertEqual(Delivery.objects.count(), 1)

    def test_a_delivery_left_behind_is_taken_up_and_the_old_log_is_pruned(self):
        now = timezone.now()
        old = Delivery.objects.create(connection=self.connection, event_id="evt_old", event_type="order.placed", status=Delivery.DELIVERED,
                                      created_at=now - datetime.timedelta(days=15))
        lost = Delivery.objects.create(connection=self.connection, event_id="evt_lost", event_type="order.ready", attempts=2,
                                       body='{"id": "evt_lost"}', next_attempt_at=now - datetime.timedelta(minutes=10))
        sender = Sender(200)
        with mock.patch("switchboard.events.post", sender), self.captureOnCommitCallbacks(execute=True):
            self.tea_order()
        self.assertFalse(Delivery.objects.filter(pk=old.pk).exists())
        lost.refresh_from_db()
        self.assertEqual((lost.status, lost.attempts), (Delivery.DELIVERED, 3))
        self.assertEqual({c["headers"]["X-Switchboard-Delivery"] for c in sender.calls} >= {"evt_lost"}, True)

    def test_two_workers_never_send_the_same_try(self):
        delivery = Delivery.objects.create(connection=self.connection, event_id="evt_x", event_type="order.placed", body="{}",
                                           next_attempt_at=timezone.now())
        mine, theirs = Delivery.objects.get(pk=delivery.pk), Delivery.objects.get(pk=delivery.pk)
        self.assertTrue(events._claim(theirs))
        self.assertFalse(events._claim(mine))  # read before the other worker took it
        sender = Sender(200)
        with mock.patch("switchboard.events.post", sender), mock.patch("switchboard.events._claim", return_value=False):
            self.assertIsNone(events.attempt(delivery.pk))
        self.assertEqual(sender.calls, [])
        self.assertEqual(Delivery.objects.get(pk=delivery.pk).attempts, 1)

    def test_the_real_sender_posts_the_body_as_it_was_signed(self):
        with mock.patch("requests.post") as http:
            http.return_value.status_code = 204
            self.assertEqual(events.post(HOOK, b'{"a": 1}', {"X-Switchboard-Event": "order.placed"}, 10), 204)
        self.assertEqual(http.call_args.args, (HOOK,))
        self.assertEqual(http.call_args.kwargs, {"data": b'{"a": 1}', "headers": {"X-Switchboard-Event": "order.placed"},
                                                 "timeout": 10, "allow_redirects": False})


class AdminPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "owner@example.org", "x", first_name="Olha", last_name="Owner")
        self.connection, self.key, _secret = connect(user=self.owner)
        self.url = reverse("admin_switchboard")

    def test_page_lists_connections_and_deliveries(self):
        Delivery.objects.create(connection=self.connection, event_id="evt_1", event_type="order.placed", summary="Order 0042 placed",
                                status=Delivery.DELIVERED, attempts=1, response_status=200)
        Delivery.objects.create(connection=self.connection, event_id="evt_2", event_type="staff.clocked_in", summary="Dana Lee clocked in",
                                status=Delivery.FAILED, attempts=5, response_status=502)
        self.client.force_login(self.owner)
        response = self.client.get(self.url, secure=True)
        self.assertEqual(response.status_code, 200)
        for text in ("https://testserver", "Holy Eucharist", "switchboard.example.org", "by Olha Owner", "Order 0042 placed",
                     "Delivered", "Dana Lee clocked in", "Failed", "Switchboard answered 502", "Nothing sent yet",
                     "1 event didn’t get through"):
            self.assertContains(response, text)
        self.assertNotContains(response, self.key)
        self.assertContains(response, 'value="ask:%d"' % self.connection.pk)
        rail = self.client.get(reverse("admin_devices"))
        self.assertContains(rail, self.url)

    def test_broken_connections_say_so(self):
        SwitchboardConnection.objects.update(broken_at=timezone.now())
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, "Broken")
        self.assertContains(response, "no longer knows this connection")
        self.assertContains(response, "Connection broken")

    def test_disconnect_asks_first_then_the_key_stops_working(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"op": f"ask:{self.connection.pk}"})
        self.assertContains(self.client.get(self.url), "Disconnect Holy Eucharist?")
        self.assertTrue(SwitchboardConnection.objects.exists())
        self.client.post(self.url, {"op": "keep"})
        self.assertNotContains(self.client.get(self.url), "Disconnect Holy Eucharist?")
        response = self.client.post(self.url, {"op": f"disconnect:{self.connection.pk}"}, follow=True)
        self.assertContains(response, "Holy Eucharist is disconnected")
        self.assertContains(response, "No Switchboard is connected")
        self.assertFalse(SwitchboardConnection.objects.exists())
        self.client.logout()
        self.assertEqual(self.client.get("/switchboard/v1/", headers={"Authorization": f"Bearer {self.key}"}).status_code, 401)

    def test_view_only_people_see_it_but_cant_disconnect(self):
        viewer = member("viewer", ["app.admin", "admin.switchboard.view"])
        self.client.force_login(viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="ask:')
        self.client.post(self.url, {"op": f"disconnect:{self.connection.pk}"})
        self.assertTrue(SwitchboardConnection.objects.exists())
        nobody = member("nobody", ["app.admin", "admin.devices.view"])
        self.client.force_login(nobody)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.assertNotContains(self.client.get(reverse("admin_devices")), self.url)

    def test_the_codes_are_on_the_groups_page(self):
        from users import access
        self.assertEqual(access.page_levels("switchboard"), ("view", "edit"))
        self.assertIn("admin.switchboard.edit", access.all_codes())
        self.assertNotIn("admin.switchboard.delete", access.all_codes())

    def test_the_key_is_only_kept_as_a_hash(self):
        self.assertEqual(self.connection.key_hash, hash_token(self.key))
        self.assertEqual(len(self.connection.key_hash), 64)
