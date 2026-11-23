"""
Switchboard's protocol, from Pavilion's side: the manifest, connecting, the bearer API with idempotency, each action,
and events with their signature, retries and log. Outgoing HTTP is faked at one seam, `switchboard.events.post`;
`background` runs straight away and `wait` only records, so deliveries play out inside the test.
"""
import contextlib
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

from catering import flow, pricing
from catering.tests import CateringFixture
from gift_cards.models import StaffCard, StaffCardType
from pos_server.models import OrderStation
from pos_server.orders import create_order
from pos_server.pricing import price_cart
from pos_server.tests import MenuFixture
from team import clock
from team.models import Shift
from users.models import GroupInfo, Role, Staff

from . import connections, contract, events, manifest
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


def broken_promises(envelope):
    """Where an event Pavilion sent differs from what its manifest says it sends."""
    trigger = next((t for t in manifest.TRIGGERS if t["type"] == envelope.get("type")), None)
    if trigger is None:
        return [f"{envelope.get('type')} is not in the manifest"]
    return ([f"{envelope['type']}: {wrong}" for wrong in contract.mismatches(trigger["fields"], envelope["data"])]
            + [f"{envelope['type']}: {key} is not in the manifest" for key in contract.undeclared(trigger["fields"], envelope["data"])])


class Sender:
    """Stands in for events.post: answers each call with the next status (the last one from then on).
    Every event that passes through is held to the manifest; EventTests fails on any that broke it."""

    broken = []

    def __init__(self, *statuses):
        self.statuses, self.calls = list(statuses) or [200], []

    def __call__(self, url, body, headers, timeout=None):
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        Sender.broken += broken_promises(json.loads(body))
        status = self.statuses[min(len(self.calls), len(self.statuses)) - 1]
        if isinstance(status, Exception):
            raise status
        return status


_seams = []
REAL_POST = events.post  # the real sender, for the one test of it


def setUpModule():
    """Nothing in these tests reaches the network or starts a thread: events an action or a page sets off are "posted" to
    a sender that only holds them to the manifest (tests that look at what was sent bring their own)."""
    for target, stand_in in (("switchboard.events.post", Sender(200)), ("switchboard.events.background", lambda job: job()),
                             ("switchboard.events.wait", lambda seconds: None)):
        _seams.append(mock.patch(target, stand_in))
        _seams[-1].start()


def tearDownModule():
    while _seams:
        _seams.pop().stop()


class Api:
    """Calls with a connection's key."""

    def call(self, name, body, key=None, idempotency=None, raw=None):
        headers = {"Authorization": f"Bearer {key or self.key}"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        response = self.client.post(f"/switchboard/v1/actions/{name}", raw if raw is not None else json.dumps(body),
                                    content_type="application/json", headers=headers)
        # Every answer is held to the manifest, whatever the test was about.
        if response.status_code == 200 and name in manifest.BY_NAME:
            output = manifest.BY_NAME[name]["output"]
            self.assertEqual(contract.mismatches(output, response.json()), [], f"{name} broke its manifest")
            self.assertEqual(contract.undeclared(output, response.json()), [], f"{name} answered fields its manifest leaves out")
        return response


class ManifestTests(TestCase):
    def test_the_manifest_keeps_its_own_rules(self):
        self.assertEqual(contract.manifest_problems(manifest.TRIGGERS, manifest.ACTIONS), [])

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
        self.assertLessEqual({"order.placed", "order.ready", "staff.clocked_in", "staff.clocked_out"}, set(triggers))
        self.assertLessEqual({"order.id", "order.number", "order.type", "order.total", "order.items", "customer.name", "customer.email"},
                             triggers["order.placed"])
        self.assertLessEqual({"staff.id", "staff.name", "staff.email", "at"}, triggers["staff.clocked_in"])
        self.assertIn("hours", triggers["staff.clocked_out"])
        actions = {a["name"]: a for a in m["actions"]}
        self.assertLessEqual({"staff.find", "staff.ensure", "staff_card.issue", "shift.add"}, set(actions))
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
        Sender.broken = []
        self.addCleanup(lambda: self.assertEqual(Sender.broken, [], "an event broke the manifest"))
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



class MoreActionTests(Api, MenuFixture, TestCase):
    """The actions added after the first four: lookups first answer `found`, actions refuse with the protocol's codes."""

    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.dana = User.objects.create_user("dana", "Dana@Example.org", "x", is_staff=True, first_name="Dana", last_name="Lee")
        cls.barista = Role.objects.get_or_create(name="Barista")[0]
        Staff.objects.create(user=cls.dana).roles.add(cls.barista)
        cls.maria = User.objects.create_user("maria", "maria@example.org", "x", first_name="Maria", last_name="Kovalenko")

    def setUp(self):
        self.connection, self.key, self.secret = connect()
        Sender.broken = []
        self.addCleanup(lambda: self.assertEqual(Sender.broken, [], "an event broke the manifest"))

    def assertError(self, response, status, code, said=""):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertIn(said, response.json()["error"]["message"])

    def ok(self, name, body=None, **more):
        response = self.call(name, body or {}, **more)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    # ----- the manifest's limits -----

    def test_limits_and_choices_in_the_manifest_are_held(self):
        self.assertError(self.call("announcement.post", {"text": "x" * 201}), 422, "invalid_params", "up to 200 characters")
        self.assertError(self.call("dish.set_availability", {"dish_id": str(self.tea.pk), "mode": "gone"}), 422, "invalid_params", "off, auto, always")
        self.assertError(self.call("store.pause_ordering", {"minutes": 500}), 422, "invalid_params", "from 0 to 240")
        self.assertError(self.call("gift_card.load", {"card_id": "1", "amount": 0, "reason": "x"}), 422, "invalid_params", "from 0.01 to 1000")

    def test_every_action_in_the_manifest_can_be_run(self):
        from . import actions
        self.assertEqual(set(actions.RUN), set(manifest.BY_NAME))
        for name, spec in manifest.BY_NAME.items():  # called with nothing: a lookup of nothing, or a 422 that says what is missing
            response = self.call(name, {})
            required = [p for p in spec["params"] if p.get("required")]
            self.assertEqual(response.status_code, 422 if required else 200, (name, response.content))
            if required:
                self.assertIn(required[0]["label"], response.json()["error"]["message"])

    # ----- staff and the schedule -----

    def test_invite_goes_to_staff_who_can_use_it(self):
        with mock.patch("misc_tools.email.send", return_value=True) as send:
            answer = self.ok("staff.invite", {"staff_id": str(self.dana.pk)}, idempotency="i-1")
        self.assertEqual(answer, {"staff_id": str(self.dana.pk), "name": "Dana Lee", "email": "Dana@example.org", "sent": True})
        self.assertEqual(send.call_args.args[:2], ("staff_invite", "Dana@example.org"))
        self.assertIn("/users/welcome/", send.call_args.args[2]["link"])
        with mock.patch("misc_tools.email.send", return_value=False):
            self.assertError(self.call("staff.invite", {"staff_id": str(self.dana.pk)}), 409, "conflict", "Email isn’t set up")
        owner = User.objects.create_superuser("boss", "boss@example.org", "x")
        self.assertError(self.call("staff.invite", {"staff_id": str(owner.pk)}), 409, "conflict", "owner")
        self.assertError(self.call("staff.invite", {"staff_id": str(self.maria.pk)}), 422, "invalid_params")  # a customer

    def test_who_is_clocked_in(self):
        self.assertEqual(self.ok("team.on_now"), {"count": 0, "names": "", "on_break": 0})
        boss = User.objects.create_superuser("boss", "boss@example.org", "x", first_name="Priya")
        clock.clock_in(self.dana, approver=boss)
        clock.clock_in(boss, approver=boss)
        clock.start_break(self.dana)
        self.assertEqual(self.ok("team.on_now"), {"count": 2, "names": "Dana Lee, Priya", "on_break": 1})

    def shift(self, days=3, start=540, end=900, published=True, user=None):
        from team import schedule
        shift = Shift(user=user or self.dana, role=self.barista, date=timezone.localdate() + datetime.timedelta(days=days), start=start, end=end,
                      published=published)
        shift.published_as = schedule.snapshot(shift) if published else None
        shift.save()
        return shift

    def test_next_shift(self):
        self.assertEqual(self.ok("shift.next", {"staff_id": str(self.dana.pk)}), {"found": False})
        self.shift(days=5)
        soon = self.shift(days=2)
        self.shift(days=1, published=False)  # not told yet: not theirs to know
        answer = self.ok("shift.next", {"staff_id": str(self.dana.pk)})
        self.assertEqual((answer["found"], answer["shift_id"], answer["role"], answer["upcoming"]), (True, str(soon.pk), "Barista", 2))
        self.assertRegex(answer["starts_at"], r"T09:00:00[+-]\d\d:00$")
        self.assertRegex(answer["ends_at"], r"T15:00:00[+-]\d\d:00$")
        self.assertError(self.call("shift.next", {"staff_id": "999999"}), 422, "invalid_params")

    def test_cancelling_a_shift_follows_the_shifts_page(self):
        told, draft = self.shift(days=2), self.shift(days=4, published=False)
        answer = self.ok("shift.cancel", {"shift_id": str(told.pk)}, idempotency="c-1")
        self.assertEqual(answer, {"shift_id": str(told.pk), "status": "waiting_for_publish", "cancelled": True})
        told.refresh_from_db()
        self.assertEqual((told.deleted, told.published), (True, False))  # gone for good once a manager publishes
        self.assertEqual(self.ok("shift.cancel", {"shift_id": str(told.pk)})["cancelled"], False)
        self.assertEqual(self.ok("shift.cancel", {"shift_id": str(draft.pk)}), {"shift_id": str(draft.pk), "status": "removed", "cancelled": True})
        self.assertFalse(Shift.objects.filter(pk=draft.pk).exists())
        self.assertEqual(self.ok("shift.cancel", {"shift_id": str(draft.pk)}), {"shift_id": str(draft.pk), "status": "removed", "cancelled": False})
        started = self.shift(days=0, start=0, end=1440)
        self.assertError(self.call("shift.cancel", {"shift_id": str(started.pk)}), 409, "conflict", "already started")
        self.assertError(self.call("shift.cancel", {"shift_id": "abc"}), 422, "invalid_params")

    def test_time_off_is_asked_for_and_waits_for_a_manager(self):
        from team.models import TimeOff
        first = timezone.localdate() + datetime.timedelta(days=10)
        body = {"staff_id": str(self.dana.pk), "first_day": first.isoformat(), "last_day": (first + datetime.timedelta(days=2)).isoformat(),
                "category": "Vacation", "reason": "Volunteering at the festival"}
        answer = self.ok("time_off.request", body, idempotency="t-1")
        request = TimeOff.objects.get()
        self.assertEqual(answer, {"request_id": str(request.pk), "status": "pending", "created": True})
        self.assertEqual((request.user, request.first_day, request.last_day, request.category, request.all_day), (self.dana, first, first + datetime.timedelta(days=2), "Vacation", True))
        self.assertEqual(self.ok("time_off.request", body, idempotency="t-2"), {"request_id": str(request.pk), "status": "pending", "created": False})
        self.assertEqual(TimeOff.objects.count(), 1)
        today = timezone.localdate().isoformat()
        self.assertError(self.call("time_off.request", {**body, "first_day": today, "last_day": today}), 422, "invalid_params", "after today")
        self.assertError(self.call("time_off.request", {**body, "first_day": "next week"}), 422, "invalid_params", "2026-12-24")
        self.assertError(self.call("time_off.request", {**body, "last_day": timezone.localdate().isoformat()}), 422, "invalid_params")

    # ----- customers and orders -----

    def tea_order(self, **fields):
        return create_order(lines=[self.dish(self.tea, 2)], channel="web", initial_status=OrderStation.STATUS_PENDING, **fields)

    def test_find_a_customer_with_what_they_spent(self):
        self.assertEqual(self.ok("customer.find", {"email": "nobody@example.org"}), {"found": False})
        order = self.tea_order(customer=self.maria)
        self.tea_order(customer=self.maria)
        answer = self.ok("customer.find", {"email": "MARIA@example.org"})
        self.assertEqual({k: answer[k] for k in ("found", "customer_id", "name", "email", "phone", "orders", "newsletter", "notes")},
                         {"found": True, "customer_id": str(self.maria.pk), "name": "Maria Kovalenko", "email": "maria@example.org", "phone": "",
                          "orders": 2, "newsletter": False, "notes": ""})
        self.assertEqual(answer["total_spent"], float(order.total) * 2)
        self.assertEqual(answer["last_order_at"][:10], timezone.localdate().isoformat())

    def test_notes_about_a_customer_add_up_to_200_characters(self):
        from users.models import Customer
        body = {"customer_id": str(self.maria.pk), "note": "Volunteer since 2026"}
        self.assertEqual(self.ok("customer.add_note", body, idempotency="n-1")["notes"], "Volunteer since 2026")
        self.assertEqual(self.ok("customer.add_note", {**body, "note": "Oat milk"}, idempotency="n-2")["notes"], "Volunteer since 2026 · Oat milk")
        self.assertEqual(self.ok("customer.add_note", body, idempotency="n-3")["notes"], "Volunteer since 2026 · Oat milk")  # said already
        self.assertEqual(Customer.objects.get(user=self.maria).staff_notes, "Volunteer since 2026 · Oat milk")
        self.assertError(self.call("customer.add_note", {**body, "note": "x" * 180}), 409, "conflict", "full")
        self.assertError(self.call("customer.add_note", {**body, "customer_id": "0"}), 422, "invalid_params")

    def test_find_an_order_by_its_number(self):
        self.assertEqual(self.ok("order.find", {"number": "9999"}), {"found": False})
        self.assertEqual(self.ok("order.find", {"number": "soon"}), {"found": False})
        order = self.tea_order(name="Maria Kovalenko", email="maria@example.org")
        answer = self.ok("order.find", {"number": f"#{order.pk:04d}"})
        self.assertEqual({k: v for k, v in answer.items() if k != "placed_at"}, {
            "found": True, "order_id": str(order.pk), "number": f"{order.pk:04d}", "type": "pickup", "channel": "web", "status": "waiting",
            "payment": "paid", "total": float(order.total), "refunded": 0.0, "items": 2, "summary": "2 × Tea",
            "customer_name": "Maria Kovalenko", "customer_email": "maria@example.org"})
        order.set_station_status(["bar"], OrderStation.STATUS_APPROVED)
        self.assertEqual(self.ok("order.find", {"number": str(order.pk)})["status"], "in_progress")
        order.set_station_status(["bar"], OrderStation.STATUS_DONE)
        self.assertEqual(self.ok("order.find", {"number": str(order.pk)})["status"], "ready")
        order.picked_up = True
        order.save()
        self.assertEqual(self.ok("order.find", {"number": str(order.pk)})["status"], "picked_up")

    def test_a_receipt_is_emailed_and_logged(self):
        order = self.tea_order(email="maria@example.org")
        with mock.patch("misc_tools.email.send", return_value=True) as send:
            answer = self.ok("order.send_receipt", {"order_id": str(order.pk)}, idempotency="r-1")
            self.ok("order.send_receipt", {"order_id": str(order.pk), "email": "other@example.org"}, idempotency="r-2")
        self.assertEqual(answer, {"order_id": str(order.pk), "email": "maria@example.org", "sent": True})
        self.assertEqual([c.args[:2] for c in send.call_args_list], [("receipt", "maria@example.org"), ("receipt", "other@example.org")])
        self.assertEqual(list(order.receipts.values_list("channel", "to")), [("email", "maria@example.org"), ("email", "other@example.org")])
        with mock.patch("misc_tools.email.send", return_value=False):
            self.assertError(self.call("order.send_receipt", {"order_id": str(order.pk)}), 409, "conflict", "Email isn’t set up")
        nameless = self.tea_order()
        self.assertError(self.call("order.send_receipt", {"order_id": str(nameless.pk)}), 422, "invalid_params", "no email")
        self.assertError(self.call("order.send_receipt", {"order_id": "999999"}), 422, "invalid_params")

    def test_a_days_sales_added_up(self):
        from pos_server.models import Order
        empty = self.ok("sales.summary", {"date": "2020-01-01"})
        self.assertEqual(empty, {"date": "2020-01-01", "orders": 0, "sales": 0.0, "average": 0.0, "tax": 0.0, "tips": 0.0, "discounts": 0.0,
                                 "refunds": 0.0, "items": 0, "top_dish": "", "online_orders": 0})
        first = self.tea_order()
        create_order(lines=[self.dish(self.tea), self.dish(self.coffee, cust={"groups": {self.size_key: [self.large.id]}})], channel="store",
                     initial_status=OrderStation.STATUS_APPROVED)
        old = self.tea_order()
        Order.objects.filter(pk=old.pk).update(timestamp=timezone.now() - datetime.timedelta(days=3))
        today = self.ok("sales.summary")
        total = sum(o.total for o in Order.objects.exclude(pk=old.pk))
        self.assertEqual((today["date"], today["orders"], today["items"], today["top_dish"], today["online_orders"]),
                         (timezone.localdate().isoformat(), 2, 4, "Tea", 1))
        self.assertEqual((today["sales"], today["average"]), (float(total), round(float(total) / 2, 2)))
        self.assertEqual(today["tax"], float(first.tax + Order.objects.exclude(pk__in=[first.pk, old.pk]).get().tax))
        self.assertError(self.call("sales.summary", {"date": "yesterday"}), 422, "invalid_params", "2026-12-24")

    # ----- menu and stock -----

    def test_find_a_dish_and_whether_it_sells(self):
        from pos_server.models import Dish
        self.assertEqual(self.ok("dish.find", {"name": "sushi"}), {"found": False, "count": 0})
        Dish.objects.create(title="Veggie burger", price=Decimal("13"), station=self.kitchen)
        answer = self.ok("dish.find", {"name": "burger"})
        self.assertEqual(answer, {"found": True, "count": 2, "dish_id": str(self.burger.pk), "name": "Burger", "price": 14.5,
                                  "station": self.kitchen.name, "available": True, "mode": "auto", "reason": "",
                                  "matches": "Burger, Veggie burger"})
        type(self.bun).objects.filter(pk=self.bun.pk).update(inventory=0)
        type(self.bun).objects.get(pk=self.bun.pk).save()
        answer = self.ok("dish.find", {"name": "Burger"})
        self.assertEqual((answer["available"], answer["reason"]), (False, "out_of_stock"))

    def test_turning_a_dish_off_and_back(self):
        from pos_server.models import Dish
        body = {"dish_id": str(self.tea.pk), "mode": "off"}
        self.assertEqual(self.ok("dish.set_availability", body, idempotency="d-1"), {"dish_id": str(self.tea.pk), "name": "Tea", "mode": "off", "available": False})
        self.assertFalse(Dish.objects.get(pk=self.tea.pk).available)
        self.assertEqual(self.ok("dish.find", {"name": "Tea"})["reason"], "turned_off")
        self.assertEqual(self.ok("dish.set_availability", {**body, "mode": "auto"}, idempotency="d-2")["available"], True)
        self.assertError(self.call("dish.set_availability", {**body, "dish_id": "999999"}), 422, "invalid_params", "Find dish")

    def test_stopping_a_station_sells_its_dishes_out(self):
        from pos_server.models import Dish, Station
        body = {"station": "BAR", "taking_tickets": False}
        with self.captureOnCommitCallbacks(execute=True):
            answer = self.ok("station.set_taking_tickets", body, idempotency="s-1")
        self.assertEqual(answer, {"station": "bar", "name": self.bar.name, "taking_tickets": False, "changed": True})
        self.assertFalse(Station.objects.get(code="bar").taking_tickets)
        self.assertFalse(Dish.objects.get(pk=self.tea.pk).available)
        self.assertEqual(self.ok("dish.find", {"name": "Tea"})["reason"], "station_paused")
        self.assertEqual(self.ok("station.set_taking_tickets", body, idempotency="s-2")["changed"], False)
        self.assertEqual(self.ok("station.set_taking_tickets", {"station": self.bar.name, "taking_tickets": True}, idempotency="s-3")["changed"], True)
        self.assertEqual(list(Delivery.objects.order_by("id").values_list("event_type", flat=True)), ["station.paused"])
        self.assertError(self.call("station.set_taking_tickets", {**body, "station": "Sushi bar"}), 422, "invalid_params", "Its stations")

    def test_checking_and_changing_stock(self):
        from pos_server.models import Ingredient
        beef = Ingredient.objects.create(title="Ground beef", unit_of_measurement="g", inventory=800, par=2000)
        Ingredient.objects.create(title="Ground pork", unit_of_measurement="g", inventory=10)
        water = Ingredient.objects.create(title="Tap water", unit_of_measurement="ml", unlimited=True)
        self.assertEqual(self.ok("stock.find", {"name": "saffron"}), {"found": False, "count": 0})
        self.assertEqual(self.ok("stock.find", {"name": "ground"}), {
            "found": True, "count": 2, "kind": "ingredient", "item_id": str(beef.pk), "name": "Ground beef", "on_hand": 800, "unit": "g",
            "par": 2000, "below_par": True, "matches": "Ground beef, Ground pork"})
        patty = self.ok("stock.find", {"name": "patty"})  # no ingredient is called that: the component
        self.assertEqual((patty["kind"], patty["item_id"], patty["on_hand"], patty["unit"]), ("component", str(self.patty.pk), 100, "ea"))
        self.assertNotIn("par", patty)
        self.assertEqual(self.ok("stock.find", {"name": "ground", "kind": "component"}), {"found": False, "count": 0})
        body = {"kind": "ingredient", "item_id": str(beef.pk), "change": 5000}
        self.assertEqual(self.ok("stock.adjust", body, idempotency="k-1"),
                         {"kind": "ingredient", "item_id": str(beef.pk), "name": "Ground beef", "on_hand": 5800, "unit": "g", "par": 2000, "below_par": False})
        self.ok("stock.adjust", body, idempotency="k-1")  # the same call again
        self.assertEqual(Ingredient.objects.get(pk=beef.pk).inventory, 5800)
        self.assertEqual(self.ok("stock.adjust", {**body, "change": -300.5}, idempotency="k-2")["on_hand"], 5499.5)
        self.assertError(self.call("stock.adjust", {**body, "change": -9000}), 409, "conflict", "can’t be taken away")
        self.assertError(self.call("stock.adjust", {**body, "change": 0}), 422, "invalid_params")
        self.assertError(self.call("stock.adjust", {**body, "item_id": str(water.pk)}), 409, "conflict", "isn’t counted")
        self.assertError(self.call("stock.adjust", {**body, "kind": "component", "item_id": "999999"}), 422, "invalid_params", "Check stock")
        self.assertEqual(self.ok("stock.adjust", {"kind": "component", "item_id": str(self.bun.pk), "change": -100}, idempotency="k-3")["on_hand"], 0)
        self.assertFalse(type(self.burger).objects.get(pk=self.burger.pk).available)  # dishes follow, as in Cookery

    # ----- gift cards -----

    def issue(self, **more):
        body = {"name": "Maya", "email": "maya@example.org", "amount": 25, "message": "Thank you!", **more}
        with mock.patch("misc_tools.listmonk.is_configured", return_value=True), mock.patch("gift_cards.giftcards.send_email", return_value=True) as send, \
                self.captureOnCommitCallbacks(execute=True):
            response = self.call("gift_card.issue", body, idempotency=more.pop("key", "g-1"))
        return response, send

    def test_a_gift_card_is_emailed_and_its_number_never_comes_back(self):
        from gift_cards.models import GiftCard
        response, send = self.issue()
        self.assertEqual(response.status_code, 200, response.content)
        card = GiftCard.objects.get()
        self.assertEqual(response.json(), {"card_id": str(card.pk), "last4": card.last4, "balance": 25.0, "amount": 25.0, "emailed": True})
        self.assertEqual((card.recipient, card.email, card.source, card.buyer, card.issued_by, card.message),
                         ("Maya", "maya@example.org", GiftCard.STAFF, "Switchboard (Holy Eucharist)", None, "Thank you!"))
        self.assertEqual(card.events.get().note, "Issued by Switchboard (Holy Eucharist)")
        kind, to, data = send.call_args.args[:3]
        number = data["card_url"].rstrip("/").rsplit("/", 1)[-1]
        self.assertEqual((kind, to, GiftCard.find(number)), ("gift_card_issued", "maya@example.org", card))  # the number: only in their email
        self.assertNotIn(number, response.content.decode())
        record = IdempotencyRecord.objects.get(key="g-1")
        self.assertNotIn(number, json.dumps(record.body))
        sent = Delivery.objects.get(event_type="gift_card.issued")
        self.assertEqual((sent.status, sent.body), (Delivery.DELIVERED, ""))
        again, send = self.issue()  # the same Idempotency-Key
        self.assertEqual(again.json(), response.json())
        send.assert_not_called()
        self.assertEqual(GiftCard.objects.count(), 1)

    def test_no_gift_card_is_made_when_it_cant_be_emailed(self):
        from gift_cards.models import GiftCard
        body = {"name": "Maya", "email": "maya@example.org", "amount": 25}
        self.assertError(self.call("gift_card.issue", body), 409, "conflict", "can’t email gift cards yet")  # email isn't connected
        with mock.patch("misc_tools.listmonk.is_configured", return_value=True), \
                mock.patch("gift_cards.giftcards.send_email", side_effect=Exception("listmonk down")), self.assertLogs("gift_cards.giftcards", "ERROR"):
            self.assertError(self.call("gift_card.issue", body, idempotency="g-2"), 409, "conflict", "no card was made")
        self.assertFalse(GiftCard.objects.exists())
        self.assertFalse(Delivery.objects.exists())  # and no "gift card issued" went out
        with mock.patch("misc_tools.listmonk.is_configured", return_value=True):
            self.assertError(self.call("gift_card.issue", {**body, "amount": 12.5}), 422, "invalid_params", "whole dollars")
            self.assertError(self.call("gift_card.issue", {**body, "amount": 5000}), 422, "invalid_params")
            self.assertError(self.call("gift_card.issue", {**body, "email": "maya"}), 422, "invalid_params", "email")

    def test_looking_up_loading_freezing_and_resending_a_gift_card(self):
        from gift_cards.models import GiftCard
        card = GiftCard.objects.create(available_balance=Decimal("25"), recipient="Maya", email="maya@example.org")
        number = card.number
        cid = str(card.pk)
        self.assertEqual(self.ok("gift_card.get", {"card_id": "999999"}), {"found": False})
        found = self.ok("gift_card.get", {"card_id": cid})
        self.assertEqual({k: v for k, v in found.items() if k != "issued_at"}, {
            "found": True, "card_id": cid, "last4": number[-4:], "balance": 25.0, "original": 25.0, "frozen": False, "name": "Maya",
            "email": "maya@example.org"})
        loaded = self.ok("gift_card.load", {"card_id": cid, "amount": 10, "reason": "Thanks for volunteering"}, idempotency="l-1")
        self.assertEqual(loaded, {"card_id": cid, "last4": number[-4:], "balance": 35.0, "added": 10.0})
        self.ok("gift_card.load", {"card_id": cid, "amount": 10, "reason": "Thanks for volunteering"}, idempotency="l-1")
        card.refresh_from_db()
        self.assertEqual((card.available_balance, card.events.get().note), (Decimal("35"), "Thanks for volunteering · Switchboard"))
        self.assertError(self.call("gift_card.load", {"card_id": cid, "amount": 990, "reason": "Too much"}), 409, "conflict", "up to $1,000")
        self.assertError(self.call("gift_card.load", {"card_id": cid, "amount": -5, "reason": "Take it back"}), 422, "invalid_params")
        self.assertError(self.call("gift_card.load", {"card_id": "999999", "amount": 5, "reason": "x"}), 422, "invalid_params", "no gift card")
        frozen = self.ok("gift_card.freeze", {"card_id": cid, "frozen": True}, idempotency="f-1")
        self.assertEqual((frozen["frozen"], frozen["changed"], GiftCard.objects.get(pk=card.pk).frozen), (True, True, True))
        self.assertEqual(self.ok("gift_card.freeze", {"card_id": cid, "frozen": True}, idempotency="f-2")["changed"], False)
        with mock.patch("gift_cards.giftcards.send_email", return_value=True) as send:
            self.assertError(self.call("gift_card.resend", {"card_id": cid}), 409, "conflict", "frozen")
            self.ok("gift_card.freeze", {"card_id": cid, "frozen": False}, idempotency="f-3")
            sent = self.ok("gift_card.resend", {"card_id": cid}, idempotency="e-1")
        self.assertEqual((sent["email"], sent["sent"]), ("maya@example.org", True))
        kind, to, data = send.call_args.args[:3]
        self.assertEqual((kind, to, data["last4"]), ("gift_card_balance", "maya@example.org", number[-4:]))
        self.assertNotIn(number, json.dumps(data))
        with mock.patch("gift_cards.giftcards.send_email", return_value=False):
            self.assertError(self.call("gift_card.resend", {"card_id": cid}), 409, "conflict", "Email isn’t set up")
        for record in IdempotencyRecord.objects.all():
            self.assertNotIn(number, json.dumps(record.body))

    # ----- coupons -----

    def test_a_coupon_is_made_with_the_coupons_pages_rules(self):
        from pos_server.models import Discount
        ends = timezone.localdate() + datetime.timedelta(days=30)
        answer = self.ok("coupon.create", {"name": "Volunteer thanks", "code": " thanks10 ", "kind": "pct", "value": 10, "ends_on": ends.isoformat(),
                                           "max_uses": 50, "once_per_customer": True, "min_subtotal": 15, "online_only": True}, idempotency="c-1")
        coupon = Discount.objects.get(code="THANKS10")
        self.assertEqual(answer, {"coupon_id": str(coupon.pk), "code": "THANKS10", "name": "Volunteer thanks"})
        self.assertEqual((coupon.kind, coupon.value, coupon.ends_on, coupon.max_uses, coupon.per_customer, coupon.min_subtotal, coupon.channels,
                          coupon.target, coupon.active, coupon.on_register, coupon.online),
                         ("pct", Decimal("10"), ends, 50, 1, Decimal("15"), ["pickup", "delivery"], "order", True, False, True))
        cart = price_cart([self.dish(self.burger, 2, cust={"groups": {self.temp_key: [self.medium.id], self.bun_key: []}})],
                          order_discount={"code": "thanks10"}, channel="web", customer=self.maria)
        self.assertEqual(cart.order_discount.amount, Decimal("2.90"))  # it works at checkout
        made_up = self.ok("coupon.create", {"kind": "amt", "value": 5}, idempotency="c-2")
        self.assertRegex(made_up["code"], r"^[A-Z0-9]{8}$")
        self.assertEqual(made_up["name"], made_up["code"])
        self.assertError(self.call("coupon.create", {"code": "THANKS10", "kind": "amt", "value": 5}), 409, "conflict", "already uses THANKS10")
        self.assertError(self.call("coupon.create", {"code": "no spaces!", "kind": "amt", "value": 5}), 422, "invalid_params")
        self.assertError(self.call("coupon.create", {"kind": "pct", "value": 150}), 422, "invalid_params", "more than 100")
        self.assertError(self.call("coupon.create", {"kind": "pct", "value": 10, "ends_on": "2020-01-01"}), 422, "invalid_params", "in the past")
        self.assertEqual(Discount.objects.count(), 2)

    def test_find_a_coupon_and_how_often_it_was_used(self):
        from pos_server.models import Discount
        self.assertEqual(self.ok("coupon.find", {"code": "nope"}), {"found": False})
        coupon = Discount.objects.create(name="Welcome", kind="amt", value=Decimal("5"), scope="order", code="welcome", online=True, times_used=14)
        self.assertEqual(self.ok("coupon.find", {"code": "welcome"}), {
            "found": True, "coupon_id": str(coupon.pk), "code": "WELCOME", "name": "Welcome", "kind": "amt", "value": 5, "active": True,
            "times_used": 14, "ends_on": ""})

    # ----- the store -----

    def test_is_the_store_open(self):
        from admin_portal import hours as hours_page
        from events.models import StoreHours
        cfg = StoreHours.load()
        cfg.week = [{"open": True, "r": [[0, 1440]]} for _day in range(7)]
        cfg.save()
        answer = self.ok("store.status")
        self.assertEqual((answer["open"], answer["ordering_paused"], answer["hours_today"]), (True, False, "12 AM – 12 AM"))
        self.assertIn("closes_at", answer)
        self.assertNotIn("opens_at", answer)
        hours_page.pause(30)
        self.assertEqual((self.ok("store.status")["ordering_paused"], "paused_until" in self.ok("store.status")), (True, True))
        cfg.week = [{"open": False, "r": []} for _day in range(7)]
        cfg.save()
        closed = self.ok("store.status")
        self.assertEqual((closed["open"], closed["hours_today"], "closes_at" in closed, "opens_at" in closed), (False, "", False, False))

    def test_pausing_and_resuming_online_orders(self):
        from events.hours import Hours
        with self.captureOnCommitCallbacks(execute=True):
            paused = self.ok("store.pause_ordering", {"minutes": 45}, idempotency="p-1")
        until = Hours().paused_until()
        self.assertEqual(paused, {"paused_until": timezone.localtime(until).isoformat(timespec="seconds")})
        self.assertAlmostEqual((until - timezone.now()).total_seconds(), 45 * 60, delta=30)
        self.assertEqual(self.ok("store.pause_ordering", {"minutes": 45}, idempotency="p-1"), paused)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.ok("store.resume_ordering", idempotency="p-2"), {"resumed": True})
        self.assertIsNone(Hours().paused_until())
        self.assertEqual(self.ok("store.resume_ordering", idempotency="p-3"), {"resumed": False})
        self.assertEqual(list(Delivery.objects.order_by("id").values_list("event_type", flat=True)), ["store.paused", "store.resumed"])
        self.assertIn("paused_until", self.ok("store.pause_ordering"))  # no minutes: until closing

    def test_a_closure_and_extra_hours_go_on_the_calendar(self):
        from events.models import Event
        day = timezone.localdate() + datetime.timedelta(days=30)
        body = {"title": "Christmas Day", "kind": "closed", "starts_on": day.isoformat(), "description": "Back on the 26th."}
        with mock.patch("events.announce.announce_later") as announce:
            answer = self.ok("store_event.create", body, idempotency="e-1")
        announce.assert_not_called()  # customers are only emailed from the Events page
        closure = Event.objects.get()
        self.assertEqual(answer, {"event_id": str(closure.pk), "created": True})
        self.assertEqual((closure.kind, closure.start_date, closure.end_date, closure.all_day, closure.dine_in, closure.pickup, closure.delivery,
                          closure.banner, closure.description), ("closed", day, day, True, True, True, True, True, "Back on the 26th."))
        self.assertEqual(self.ok("store_event.create", body, idempotency="e-2"), {"event_id": str(closure.pk), "created": False})
        party = self.ok("store_event.create", {"title": "Parish dinner", "kind": "open", "starts_on": day.isoformat(),
                                               "ends_on": (day + datetime.timedelta(days=1)).isoformat(), "opens_at": "17:00", "closes_at": "22:30",
                                               "pickup": False, "banner": False}, idempotency="e-3")
        dinner = Event.objects.get(pk=party["event_id"])
        self.assertEqual((dinner.kind, dinner.opens, dinner.closes, dinner.all_day, dinner.dine_in, dinner.pickup, dinner.delivery, dinner.banner,
                          dinner.end_date), ("open", 1020, 1350, False, True, False, False, False, day + datetime.timedelta(days=1)))
        self.assertError(self.call("store_event.create", {**body, "kind": "open", "title": "No hours"}), 422, "invalid_params", "From and To")
        self.assertError(self.call("store_event.create", {**body, "title": "Half", "opens_at": "09:00"}), 422, "invalid_params", "both")
        self.assertError(self.call("store_event.create", {**body, "title": "Backwards", "opens_at": "21:00", "closes_at": "09:00"}), 422, "invalid_params")
        self.assertError(self.call("store_event.create", {**body, "title": "Odd", "opens_at": "9am", "closes_at": "10:00"}), 422, "invalid_params", "09:00")
        self.assertError(self.call("store_event.create", {**body, "title": "Nothing", "dine_in": False, "pickup": False, "delivery": False}),
                         422, "invalid_params", "at least one")
        self.assertError(self.call("store_event.create", {**body, "title": "Backwards days", "ends_on": "2020-01-01"}), 422, "invalid_params")
        self.assertEqual(Event.objects.count(), 2)

    def test_an_announcement_shows_on_the_screens_and_is_read_out(self):
        from django.core.cache import cache

        from screens import popups
        cache.delete(popups.EVENTS_KEY)
        with mock.patch("audio.calls.speak") as speak:
            self.assertEqual(self.ok("announcement.post", {"text": "Volunteer lunch is ready"}, idempotency="a-1"), {"posted": True})
            self.ok("announcement.post", {"text": "Volunteer lunch is ready"}, idempotency="a-1")  # not announced twice
            self.ok("announcement.post", {"text": "Quiet one", "speak": False}, idempotency="a-2")
        said = [e["data"] for e in cache.get(popups.EVENTS_KEY) if e["trigger"] == "announcement"]
        self.assertEqual(said, [{"text": "Volunteer lunch is ready", "by": "Holy Eucharist"}, {"text": "Quiet one", "by": "Holy Eucharist"}])
        speak.assert_called_once_with("Volunteer lunch is ready", "announcement")


@mock.patch("catering.flow.send_email")
class CateringActionTests(Api, CateringFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()

    def setUp(self):
        self.connection, self.key, self.secret = connect()

    def test_find_a_catering_order(self, _send):
        order = self.make_order()
        self.assertEqual(self.call("catering.find", {"number": "C-9999"}).json(), {"found": False})
        self.assertEqual(self.call("catering.find", {"number": "lunch"}).json(), {"found": False})
        for typed in (order.number, order.number.lower(), order.number[2:]):
            answer = self.call("catering.find", {"number": typed}).json()
            self.assertEqual({k: v for k, v in answer.items() if k != "event_at"}, {
                "found": True, "catering_id": str(order.pk), "number": order.number, "title": "Office lunch", "status": "new",
                "fulfilment": "pickup", "total": 688.0, "customer_name": "Ben Carter", "customer_email": "ben@example.com"})

    def test_a_message_from_the_store_is_emailed_to_the_customer(self, send):
        from catering.models import CateringMessage, CateringOrder
        order = self.make_order()
        body = {"number": order.number, "message": "Your volunteers eat free that day."}
        response = self.call("catering.send_message", body, idempotency="m-1")
        self.assertEqual(response.json(), {"catering_id": str(order.pk), "number": order.number, "sent": True})
        self.call("catering.send_message", body, idempotency="m-1")
        message = order.messages.get()
        self.assertEqual((message.who, message.text), (CateringMessage.STAFF, "Your volunteers eat free that day."))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[:2], ("catering_message", "ben@example.com"))
        CateringOrder.objects.filter(pk=order.pk).update(status=CateringOrder.DONE)
        closed = self.call("catering.send_message", body, idempotency="m-2")
        self.assertEqual((closed.status_code, closed.json()["error"]["code"]), (409, "conflict"))
        missing = self.call("catering.send_message", {**body, "number": "C-9999"}, idempotency="m-3")
        self.assertEqual((missing.status_code, missing.json()["error"]["code"]), (422, "invalid_params"))


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
        Sender.broken = []
        self.addCleanup(lambda: self.assertEqual(Sender.broken, [], "an event broke the manifest"))
        self.waits = []
        patcher = mock.patch("switchboard.events.wait", self.waits.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self, sender, n=0):
        call = sender.calls[n]
        return call, json.loads(call["body"])

    @staticmethod
    def types(sender):
        return [c["headers"]["X-Switchboard-Event"] for c in sender.calls]

    def one(self, sender, kind):
        """The one event of this type that was sent."""
        found = [json.loads(c["body"]) for c in sender.calls if c["headers"]["X-Switchboard-Event"] == kind]
        self.assertEqual(len(found), 1, f"{kind}: {self.types(sender)}")
        return found[0]

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
            "order": {"id": str(order.pk), "number": f"{order.pk:04d}", "type": "pickup", "channel": "web", "total": float(order.total), "items": 2,
                      "subtotal": 4.0, "discount": 0.0, "tax": float(order.tax), "tip": 0.0, "summary": "2 × Tea",
                      "placed_at": timezone.localtime(order.timestamp).isoformat(timespec="seconds")},
            "customer": {"name": "Maria Kovalenko", "email": "maria@example.org", "phone": ""}})
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
        self.assertEqual(first["data"]["customer"], {"name": "Ana B", "email": "ana@example.org", "phone": ""})  # from their account
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
            self.assertNotIn("order.ready", self.types(sender))  # the kitchen is still on it
            with self.captureOnCommitCallbacks(execute=True):
                order.set_station_status(["kitchen"], OrderStation.STATUS_DONE)
        self.assertEqual(self.types(sender).count("order.ready"), 1)
        event = self.one(sender, "order.ready")
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
        self.assertEqual(self.types(sender), ["staff.clocked_in", "staff.break_started", "staff.break_ended", "staff.clocked_out"])
        _call, came = self.sent(sender, 0)
        staff = {"id": str(self.dana.pk), "name": "Dana Lee", "email": "dana@example.org"}
        self.assertEqual(came["subject"], {"type": "person", **staff})
        self.assertEqual(came["data"], {"staff": staff, "at": start.isoformat(), "role": "Barista"})
        self.assertEqual(came["occurred_at"], start.isoformat())
        self.assertRegex(came["data"]["at"], r"^2026-12-05T09:00:00[+-]\d\d:00$")  # the store's time, with its offset
        left = self.one(sender, "staff.clocked_out")
        self.assertEqual(left["data"], {"staff": staff, "at": (start + datetime.timedelta(hours=5, minutes=45)).isoformat(), "role": "Barista", "hours": 5.25})
        self.assertEqual(self.one(sender, "staff.break_started")["data"]["at"], (start + datetime.timedelta(hours=2)).isoformat())
        back = self.one(sender, "staff.break_ended")
        self.assertEqual((back["data"]["minutes"], back["subject"]["email"]), (30, "dana@example.org"))

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
                                       body='{"id": "evt_lost", "type": "order.ready", "data": {}}', next_attempt_at=now - datetime.timedelta(minutes=10))
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
            self.assertEqual(REAL_POST(HOOK, b'{"a": 1}', {"X-Switchboard-Event": "order.placed"}, 10), 204)
        self.assertEqual(http.call_args.args, (HOOK,))
        self.assertEqual(http.call_args.kwargs, {"data": b'{"a": 1}', "headers": {"X-Switchboard-Event": "order.placed"},
                                                 "timeout": 10, "allow_redirects": False})


class Events:
    """For tests of what Pavilion sends: a connected Switchboard, a sender that holds every event to the manifest, and
    `sending()` to run something and look at what went out."""

    def setUp(self):
        super().setUp()
        self.connection, self.key, self.secret = connect()
        Sender.broken = []
        self.addCleanup(lambda: self.assertEqual(Sender.broken, [], "an event broke the manifest"))
        quiet = self.assertNoLogs("switchboard.events", "ERROR")  # an event that couldn't be built is only logged: fail on it
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    @contextlib.contextmanager
    def sending(self):
        sender = Sender(200)
        with mock.patch("switchboard.events.post", sender), self.captureOnCommitCallbacks(execute=True):
            yield sender

    @staticmethod
    def types(sender):
        return [c["headers"]["X-Switchboard-Event"] for c in sender.calls]

    def every(self, sender, kind):
        return [json.loads(c["body"]) for c in sender.calls if c["headers"]["X-Switchboard-Event"] == kind]

    def one(self, sender, kind):
        found = self.every(sender, kind)
        self.assertEqual(len(found), 1, f"{kind}: {self.types(sender)}")
        return found[0]


class OrderEventTests(Events, MenuFixture, TestCase):
    """Orders after they're placed: the kitchen's steps, rejecting, picking up, refunds, discounts, deliveries."""

    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.boss = User.objects.create_superuser("boss", "boss@example.org", "x", first_name="Priya", last_name="Shah")

    def online(self, channel="web", **fields):
        fields = {"name": "Maria Kovalenko", "email": "maria@example.org", **fields}
        return create_order(lines=[self.dish(self.tea, 2), self.dish(self.burger, cust={"groups": {self.temp_key: [self.medium.id], self.bun_key: []}})],
                            channel=channel, initial_status=OrderStation.STATUS_PENDING, **fields)

    def kds(self, order, action, stations=("kitchen", "bar"), **extra):
        self.client.force_login(self.boss)
        return self.client.post(reverse("order_marking"), json.dumps({"orderId": order.id, "action": action, "filters": list(stations), **extra}),
                                content_type="application/json")

    def test_the_kitchen_approves_once_and_each_station_says_when_it_is_done(self):
        with self.sending() as sender:
            order = self.online()
        with self.sending() as sender:
            order.set_station_status(["kitchen"], OrderStation.STATUS_APPROVED)
        self.assertEqual(self.types(sender), [])  # the bar hasn't confirmed yet
        with self.sending() as sender:
            order.set_station_status(["bar"], OrderStation.STATUS_APPROVED)
            order.set_station_status(["bar", "kitchen"], OrderStation.STATUS_APPROVED)  # a repeat changes nothing
        approved = self.one(sender, "order.approved")
        self.assertEqual((approved["data"]["order"]["number"], approved["subject"]["email"]), (f"{order.pk:04d}", "maria@example.org"))
        with self.sending() as sender:
            order.set_station_status(["bar"], OrderStation.STATUS_DONE)
        self.assertEqual(self.types(sender), ["order.station_done"])
        self.assertEqual(self.one(sender, "order.station_done")["data"]["station"], {"code": "bar", "name": self.bar.name})
        with self.sending() as sender:
            order.set_station_status(["kitchen", "bar"], OrderStation.STATUS_DONE, only_from=[OrderStation.STATUS_APPROVED])
        self.assertEqual(sorted(self.types(sender)), ["order.ready", "order.station_done"])
        self.assertEqual(self.one(sender, "order.station_done")["data"]["station"]["code"], "kitchen")

    def test_a_register_order_is_never_approved_because_it_starts_that_way(self):
        with self.sending() as sender:
            order = create_order(lines=[self.dish(self.tea)], channel="store", initial_status=OrderStation.STATUS_APPROVED)
            order.set_station_status(["bar"], OrderStation.STATUS_DONE)
        self.assertEqual(sorted(self.types(sender)), ["order.placed", "order.ready", "order.station_done"])

    def test_rejecting_tells_why_before_the_order_is_gone(self):
        order = self.online()
        with self.sending() as sender:
            response = self.kds(order, "reject", reasons=[], text="No brioche buns left")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(type(order).objects.filter(pk=order.pk).exists())
        event = self.one(sender, "order.rejected")
        self.assertEqual(event["data"]["reason"], "No brioche buns left")
        self.assertEqual((event["data"]["order"]["id"], event["data"]["order"]["items"], event["data"]["customer"]["email"]),
                         (str(order.pk), 3, "maria@example.org"))

    def test_a_rejection_that_fails_sends_nothing(self):
        order = self.online()
        with self.sending() as sender:
            self.assertEqual(self.kds(order, "reject", reasons=[], text="").status_code, 400)
        self.assertEqual(self.types(sender), [])

    def test_picked_up_once(self):
        order = self.online()
        order.set_station_status(["kitchen", "bar"], OrderStation.STATUS_APPROVED)
        order.set_station_status(["kitchen", "bar"], OrderStation.STATUS_DONE)
        with self.sending() as sender:
            self.assertEqual(self.kds(order, "pickup").status_code, 200)
            self.kds(order, "pickup")  # a second tap
        self.assertEqual(self.types(sender), ["order.picked_up"])
        self.assertEqual(self.one(sender, "order.picked_up")["data"]["order"]["id"], str(order.pk))

    def test_a_refund_says_how_much_why_and_who(self):
        from pos_server.models import CashDrawerSession, Refund
        from pos_server.refunds import refund_order
        drawer = CashDrawerSession.objects.create(opening_amount=Decimal("100"), opened_by=self.boss)
        total = price_cart([self.dish(self.tea, 2)], channel="store").total
        order = create_order(lines=[self.dish(self.tea, 2)], channel="store", initial_status=OrderStation.STATUS_APPROVED, drawer=drawer,
                             payments=[{"method": "cash", "amount": str(total)}], email="maria@example.org")
        with self.sending() as sender, mock.patch("pos_server.order_emails.refunded"), mock.patch("online_store.order_push.refunded"):
            refund = refund_order(order, whole=True, reason="Wrong order", to=Refund.CASH, user=self.boss)
        event = self.one(sender, "order.refunded")
        self.assertEqual(event["data"]["refund"], {"id": str(refund.pk), "amount": float(total), "reason": "Wrong order", "to": "cash",
                                                   "whole": True, "by": "Priya Shah"})
        moved = self.one(sender, "drawer.cash_moved")  # the cash came out of the drawer
        self.assertEqual((moved["data"]["amount"], moved["data"]["staff"]["email"]), (-float(total), "boss@example.org"))

    def test_every_discount_on_a_new_order_is_told(self):
        from pos_server.models import Discount
        coupon = Discount.objects.create(name="Welcome", kind="amt", value=Decimal("1"), scope="order", code="welcome", online=True)
        offer = Discount.objects.create(name="Student", kind="pct", value=Decimal("10"))
        with self.sending() as sender:
            order = create_order(lines=[self.dish(self.tea, 2, discount={"offer": offer.id})], channel="store",
                                 initial_status=OrderStation.STATUS_APPROVED, order_discount={"code": "WELCOME"})
            create_order(lines=[self.dish(self.tea)], channel="store", initial_status=OrderStation.STATUS_APPROVED,
                         order_discount={"manual": {"kind": "amt", "value": "0.50"}}, can_manual_discount=True)
            create_order(lines=[self.dish(self.tea)], channel="store", initial_status=OrderStation.STATUS_APPROVED)  # none: nothing sent
        used = {e["data"]["discount"]["kind"]: e["data"] for e in self.every(sender, "discount.used")}
        self.assertEqual(set(used), {"coupon", "offer", "manual"})
        self.assertEqual(used["coupon"]["discount"], {"id": str(coupon.pk), "name": "Welcome", "code": "WELCOME", "kind": "coupon", "amount": 1.0})
        self.assertEqual((used["offer"]["discount"]["amount"], used["offer"]["order"]["id"]), (0.4, str(order.pk)))
        self.assertEqual((used["manual"]["discount"]["id"], used["manual"]["discount"]["amount"]), ("", 0.5))

    @mock.patch("deliveries.signals.trigger_push_notifications")
    def test_a_delivery_from_the_courier_taking_it_to_the_door(self, _push):
        from deliveries.models import CourierShift, Delivery as Trip
        order = self.online("delivery", to_go_order=True)
        trip = Trip.objects.create(order=order, destination="2145 W 4th Ave, Vancouver, BC V6K 1N7", address_2="Unit 204", phone=6045550147)
        CourierShift.objects.create(user=self.boss)
        self.client.force_login(self.boss)
        with self.sending() as sender:
            self.client.post(reverse("delivery_order", args=[trip.pk]), {"action": "accept"})
            self.client.post(reverse("delivery_order", args=[trip.pk]), {"action": "accept"})  # already theirs
        taken = self.one(sender, "delivery.assigned")
        self.assertEqual(taken["data"]["staff"], {"id": str(self.boss.pk), "name": "Priya Shah", "email": "boss@example.org"})
        self.assertEqual(taken["data"]["delivery"], {"id": str(trip.pk), "address": "2145 W 4th Ave, Unit 204, Vancouver, BC V6K 1N7"})
        self.assertEqual((taken["data"]["order"]["type"], taken["subject"]["email"]), ("delivery", "maria@example.org"))
        order.set_station_status(["kitchen", "bar"], OrderStation.STATUS_APPROVED)
        order.set_station_status(["kitchen", "bar"], OrderStation.STATUS_DONE)
        with self.sending() as sender:
            self.client.post(reverse("delivery_current"), {"action": "pickup"})
        self.assertEqual(sorted(self.types(sender)), ["delivery.picked_up", "order.picked_up"])
        with self.sending() as sender:
            self.client.post(reverse("delivery_current"), {"action": "arrive"})
            self.client.post(reverse("delivery_current"), {"action": "complete", "handoff": "hand"})
        done = self.one(sender, "delivery.delivered")
        self.assertEqual((done["data"]["delivery"]["handoff"], done["data"]["delivery"]["minutes"], done["data"]["order"]["id"]),
                         ("hand", 0, str(order.pk)))


@mock.patch("catering.flow.send_email")
class CateringEventTests(Events, CateringFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build()

    def test_a_request_accepted_then_completed(self, _send):
        from catering.models import CateringOrder
        order = self.make_order(days=10, organization="Juniper Community", phone="604 555 0181")
        with self.sending() as sender:
            flow.place(order)
        asked = self.one(sender, "catering.requested")
        self.assertEqual(asked["subject"], {"type": "catering_order", "id": str(order.pk), "email": "ben@example.com", "name": "Ben Carter"})
        self.assertEqual(asked["data"]["catering"], {
            "id": str(order.pk), "number": order.number, "title": "Office lunch", "status": "new", "fulfilment": "pickup", "total": 688.0,
            "rush": False, "event_at": timezone.make_aware(datetime.datetime.combine(order.event_date, datetime.time(12, 0))).isoformat()})
        self.assertEqual(asked["data"]["customer"], {"name": "Ben Carter", "email": "ben@example.com", "phone": "604 555 0181",
                                                     "organization": "Juniper Community"})
        with self.sending() as sender:
            flow.accept(order, self.owner, "See you there")
        confirmed = self.one(sender, "catering.confirmed")
        self.assertEqual((confirmed["data"]["confirmed_by"], confirmed["data"]["catering"]["status"]), ("store", "confirmed"))
        booked = self.every(sender, "service.booked")  # one per service line
        self.assertEqual(sorted(b["data"]["booking"]["service"] for b in booked), ["Chafer", "Delivery", "Server"])
        self.assertEqual({(b["data"]["booking"]["catering_number"], b["data"]["customer"]["email"]) for b in booked}, {(order.number, "ben@example.com")})
        self.assertNotIn("catering.deposit_held", self.types(sender))  # the event is 10 days off
        with self.sending() as sender:
            flow.accept(self.make_order(days=1), self.owner)  # tomorrow: the deposit is held right away
        self.assertEqual(self.one(sender, "catering.deposit_held")["data"]["deposit"], 120.0)
        with self.sending() as sender:
            flow.finish(CateringOrder.objects.get(pk=order.pk), self.owner)
        done = self.one(sender, "catering.completed")
        self.assertEqual((done["data"]["deposit_kept"], done["data"]["catering"]["status"]), (False, "done"))

    def test_changes_go_back_and_forth(self, _send):
        from catering.models import CateringMessage
        order = self.make_order()
        draft = pricing.lines_of(order)
        draft[0]["quantity"], draft[0]["price"] = 45, "11.00"
        with self.sending() as sender:
            flow.propose(order, self.owner, draft, "Bigger lunch")
        proposed = self.one(sender, "catering.changes_proposed")
        self.assertEqual((proposed["data"]["previous_total"], proposed["data"]["message"], proposed["data"]["catering"]["status"]),
                         (688.0, "Bigger lunch", "waiting"))
        self.assertGreater(proposed["data"]["catering"]["total"], 688.0)
        draft = pricing.lines_of(order)
        draft[0]["quantity"] = 30
        with self.sending() as sender:
            flow.counter(order, draft, "Thirty is enough")
            flow.write(order, CateringMessage.CUSTOMER, "Can we move pickup to 11:30?")
            flow.write(order, CateringMessage.STAFF, "Sure")  # the store's own messages aren't events
        self.assertEqual(self.one(sender, "catering.counter_proposed")["data"]["message"], "Thirty is enough")
        self.assertEqual(self.one(sender, "catering.message_received")["data"]["message"], "Can we move pickup to 11:30?")
        draft = pricing.lines_of(order)
        draft[0]["quantity"] = 35
        flow.propose(order, self.owner, draft, "Meet in the middle")
        with self.sending() as sender:
            flow.agree(order)
        self.assertEqual(self.one(sender, "catering.confirmed")["data"]["confirmed_by"], "customer")

    def test_rejected_and_cancelled(self, _send):
        with self.sending() as sender:
            flow.reject(self.make_order(), self.owner, "Fully booked", "Sorry, we can't that day")
        self.assertEqual(self.one(sender, "catering.rejected")["data"]["reason"], "Fully booked")
        with self.sending() as sender:
            flow.customer_cancel(self.make_order())
        gone = self.one(sender, "catering.cancelled")
        self.assertEqual((gone["data"]["cancelled_by"], gone["data"]["refunded"], gone["data"]["catering"]["status"]), ("customer", 0.0, "cancelled"))
        confirmed = self.make_order()
        flow.accept(confirmed, self.owner)
        with self.sending() as sender:
            flow.reject(confirmed, self.owner, "Other", "Kitchen flooded, so sorry")
        gone = self.one(sender, "catering.cancelled")
        self.assertEqual((gone["data"]["cancelled_by"], gone["data"]["refunded"], gone["data"]["reason"]), ("store", 688.0, "Other"))

    def test_a_card_that_declines_sends_nothing(self, _send):
        from payments import square
        order = self.make_order(card="ccof:real")
        with self.sending() as sender, mock.patch("payments.square.charge_card", side_effect=square.SquareError([{"detail": "Card declined"}])):
            with self.assertRaises(Exception):
                flow.accept(order, self.owner)
        self.assertEqual(self.types(sender), [])


class StoreEventTests(Events, MenuFixture, TestCase):
    """Gift and staff cards, the menu and stock, cash, people, the schedule and the store itself."""

    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.boss = User.objects.create_superuser("boss", "boss@example.org", "x", first_name="Priya", last_name="Shah")
        cls.dana = User.objects.create_user("dana", "dana@example.org", "x", is_staff=True, first_name="Dana", last_name="Lee")
        cls.alex = User.objects.create_user("alex", "alex@example.org", "x", is_staff=True, first_name="Alex", last_name="P")
        cls.barista = Role.objects.get_or_create(name="Barista")[0]
        for user in (cls.dana, cls.alex):
            Staff.objects.create(user=user).roles.add(cls.barista)

    # ----- gift cards -----

    def test_a_gift_card_from_sale_to_empty_without_its_number(self):
        from gift_cards import giftcards as gc
        from gift_cards.models import GiftCard, GiftCardDesign
        design = GiftCardDesign.objects.create(name="Birthday", amounts=[25, 50])
        with self.sending() as sender:
            order = create_order(lines=[], channel="web", initial_status=OrderStation.STATUS_PENDING, name="Alex P",
                                 gift_sales=[{"design": design.id, "amount": "25", "to": "Maya", "email": "maya@example.org"}])
        card, number = order.new_gift_cards[0]
        sold = self.one(sender, "gift_card.sold")
        self.assertEqual(sold["data"], {"card": {"id": str(card.pk), "last4": number[-4:], "balance": 25.0, "design": "Birthday"},
                                        "customer": {"name": "Maya", "email": "maya@example.org"},
                                        "amount": 25.0, "source": "online", "buyer": "Alex P", "order_id": str(order.pk)})
        self.assertEqual(sold["subject"], {"type": "gift_card", "id": str(card.pk), "email": "maya@example.org", "name": "Maya"})
        with self.sending() as sender:
            card.charge_card(Decimal("10"), order=order)
        used = self.one(sender, "gift_card.used")
        self.assertEqual((used["data"]["amount"], used["data"]["card"]["balance"], used["data"]["order_id"]), (10.0, 15.0, str(order.pk)))
        self.assertEqual(self.types(sender), ["gift_card.used"])
        with self.sending() as sender:
            card.load_card(Decimal("5"), order=order)
            gc.adjust(card, Decimal("2.50"), "Sorry about the wait", self.boss)
        loaded = self.every(sender, "gift_card.loaded")
        self.assertEqual([(e["data"]["amount"], e["data"]["reason"], e["data"]["card"]["balance"]) for e in loaded],
                         [(5.0, "refund", 20.0), (2.5, "adjustment", 22.5)])
        card = GiftCard.objects.get(pk=card.pk)
        with self.sending() as sender:
            card.charge_card(Decimal("22.50"))
            gc.set_frozen(card, True, self.boss)  # a note in its history, not an event
        self.assertEqual(self.types(sender), ["gift_card.used", "gift_card.emptied"])
        self.assertEqual(self.one(sender, "gift_card.emptied")["data"]["card"]["balance"], 0.0)
        for call in sender.calls:
            self.assertNotIn(number, call["body"].decode())

    def test_a_gift_card_the_store_gives_away(self):
        from gift_cards import giftcards as gc
        with self.sending() as sender:
            card, number = gc.issue(design=None, amount=Decimal("40"), recipient="Maya", email="maya@example.org", message="", note="", by=self.boss)
        issued = self.one(sender, "gift_card.issued")
        self.assertEqual((issued["data"]["amount"], issued["data"]["issued_by"], issued["data"]["card"]["last4"]), (40.0, "Priya Shah", card.last4))
        self.assertNotIn(number, sender.calls[0]["body"].decode())

    # ----- staff cards -----

    def test_staff_cards_issued_and_refilled(self):
        meal = StaffCardType.objects.create(name="Shift meal", kind=StaffCardType.BALANCE, amount=Decimal("12"), refill="shift", leftover="reset",
                                            cap=Decimal("12"), channels=["store"])
        with self.sending() as sender:
            card = StaffCard.objects.create(type=meal, user=self.dana, number=StaffCard.new_number(), balance=Decimal("3"))
        issued = self.one(sender, "staff_card.issued")
        self.assertEqual(issued["data"], {"staff": {"id": str(self.dana.pk), "name": "Dana Lee", "email": "dana@example.org"},
                                          "card": {"id": str(card.pk), "label": "Shift meal", "kind": "balance", "balance": 3.0}})
        with self.sending() as sender:
            clock.clock_in(self.dana, approver=self.boss)
        refilled = self.one(sender, "staff_card.refilled")
        self.assertEqual((refilled["data"]["card"]["balance"], refilled["subject"]["email"]), (12.0, "dana@example.org"))

    # ----- menu and stock -----

    def test_a_dish_sells_out_and_comes_back(self):
        from inventory.availability import apply_changes
        from pos_server.models import Component, Dish
        with self.sending() as sender:
            bun = Component.objects.get(pk=self.bun.pk)
            bun.inventory = 0
            bun.save()
        out = self.one(sender, "dish.sold_out")
        self.assertEqual(out["data"], {"dish": {"id": str(self.burger.pk), "name": "Burger", "station": self.kitchen.name, "price": 14.5},
                                       "reason": "out_of_stock"})
        self.assertEqual(self.one(sender, "stock.ran_out")["data"]["item"]["name"], "Bun")
        with self.sending() as sender:
            bun = Component.objects.get(pk=self.bun.pk)
            bun.inventory = 40
            bun.save()
            bun.save()  # saved again without a change: nothing more
        self.assertEqual(self.types(sender), ["dish.back_in_stock"])
        self.assertNotIn("reason", self.one(sender, "dish.back_in_stock")["data"])
        with self.sending() as sender:
            apply_changes([{"kind": "dish", "id": self.tea.pk, "mode": "off"}])
        self.assertEqual(self.one(sender, "dish.sold_out")["data"]["reason"], "turned_off")
        with self.sending() as sender:
            apply_changes([{"kind": "component", "id": self.patty.pk, "mode": "off"}])
        self.assertEqual(self.one(sender, "dish.sold_out")["data"]["reason"], "part_turned_off")
        with self.sending() as sender:  # archiving and new dishes are not "sold out"
            tea = Dish.objects.get(pk=self.tea.pk)
            tea.archived = True
            tea.save()
            Dish.objects.create(title="Soup", price=Decimal("6"), station=self.kitchen, availability="off")
        self.assertEqual(self.types(sender), [])

    def test_selling_the_last_one_says_so(self):
        from pos_server.models import Component
        Component.objects.filter(pk=self.bun.pk).update(inventory=1)
        with self.sending() as sender:
            create_order(lines=[self.dish(self.burger, cust={"groups": {self.temp_key: [self.medium.id], self.bun_key: []}})],
                         channel="store", initial_status=OrderStation.STATUS_APPROVED)
            create_order(lines=[self.dish(self.coffee, cust={"groups": {self.size_key: [self.large.id]}})], channel="store",
                         initial_status=OrderStation.STATUS_APPROVED)  # its espresso is made to order: never "ran out"
        self.assertEqual(self.one(sender, "stock.ran_out")["data"]["item"], {"kind": "component", "id": str(self.bun.pk), "name": "Bun",
                                                                            "on_hand": 0, "unit": "ea"})
        self.assertEqual(self.one(sender, "dish.sold_out")["data"]["dish"]["name"], "Burger")

    def test_stock_crossing_its_par_is_told_once(self):
        from pos_server.models import Ingredient
        beef = Ingredient.objects.create(title="Ground beef", unit_of_measurement="g", inventory=2500, par=2000)
        water = Ingredient.objects.create(title="Tap water", unit_of_measurement="ml", inventory=5, par=10, unlimited=True)

        def take(ingredient, amount):
            row = Ingredient.objects.get(pk=ingredient.pk)
            row.inventory -= amount
            row.save()

        with self.sending() as sender:
            take(beef, 200)   # 2300: still above
            take(beef, 500)   # 1800: below par
            take(beef, 300)   # 1500: already below, nothing more
            take(water, 5)
        low = self.one(sender, "stock.low")
        self.assertEqual(low["data"]["item"], {"kind": "ingredient", "id": str(beef.pk), "name": "Ground beef", "on_hand": 1800, "par": 2000, "unit": "g"})
        self.assertEqual(self.types(sender), ["stock.low"])
        with self.sending() as sender:
            take(beef, -1000)  # a delivery: going up is no event
            take(beef, 2500)
        self.assertEqual(self.types(sender), ["stock.ran_out"])  # through the par and out in one go: the stronger one
        with self.sending() as sender:  # packaging counted off an order with an update()
            Ingredient.objects.filter(pk=beef.pk).update(inventory=2100)
            Ingredient.objects.filter(pk=beef.pk).update(inventory=1900)
            events.stock_drawn(Ingredient, beef.pk, 200)
        self.assertEqual(self.one(sender, "stock.low")["data"]["item"]["on_hand"], 1900)
        self.assertEqual(self.types(sender), ["stock.low"])

    def test_a_batch_cooked_and_shopping_logged(self):
        from inventory import cookery
        from inventory.models import CookingSession, YieldEntry
        from pos_server.models import Ingredient
        session = CookingSession.objects.create(kind=CookingSession.COOK, component=self.patty, started_by=self.dana, holder=self.dana,
                                                started_at=timezone.now() - datetime.timedelta(minutes=25))
        YieldEntry.objects.create(session=session, amount=24)
        with self.sending() as sender:
            cookery.act(self.dana, {"action": "finish", "session": session.pk})
        cooked = self.one(sender, "batch.cooked")
        self.assertEqual(cooked["data"], {"staff": {"id": str(self.dana.pk), "name": "Dana Lee", "email": "dana@example.org"},
                                          "item": {"id": str(self.patty.pk), "name": "Patty", "unit": "ea", "on_hand": 124}, "made": 24, "minutes": 25})
        Ingredient.objects.create(title="Milk", unit_of_measurement="ml", inventory=0)
        self.client.force_login(self.boss)
        with self.sending() as sender:
            self.client.post(reverse("log_shopping"), {"items-total": "1", "item-0-title": "Milk", "item-0-quantity": "4000"})
        logged = self.one(sender, "stock.received")
        self.assertEqual((logged["data"]["items"], logged["data"]["summary"]), (1, "Milk (4000 ml)"))

    def test_a_restock_placed_then_delivered(self):
        from admin_portal import restocks
        from inventory.models import Restock, RestockLine
        from pos_server.models import Ingredient, Supplier
        milk = Ingredient.objects.create(title="Milk", unit_of_measurement="ml", inventory=0, pack_size=1000)
        order = Restock.objects.create(supplier=Supplier.objects.create(name="Fraser Valley Dairy"))
        RestockLine.objects.create(restock=order, ingredient=milk, cases=4, case_price=Decimal("5.00"), pack_size=1000)
        with self.sending() as sender:
            restocks.save_draft({**restocks.draft_from_restock(order), "status": Restock.PLACED, "placed_on": timezone.localdate().isoformat()}, order)
            restocks.save_draft(restocks.draft_from_restock(order), order)  # saved again as placed: not placed twice
        placed = self.one(sender, "restock.placed")
        self.assertEqual({k: placed["data"]["restock"][k] for k in ("number", "supplier", "total", "lines", "summary")},
                         {"number": order.number, "supplier": "Fraser Valley Dairy", "total": 20.0, "lines": 1, "summary": "4 × Milk"})
        with self.sending() as sender:
            restocks.save_draft({**restocks.draft_from_restock(order), "status": Restock.DELIVERED, "delivered_on": timezone.localdate().isoformat()}, order)
            restocks.save_draft(restocks.draft_from_restock(order), order)
        self.assertEqual(self.types(sender), ["restock.delivered"])
        self.assertEqual(Ingredient.objects.get(pk=milk.pk).inventory, 4000)

    def test_a_station_switched_off_and_on_from_the_stations_page(self):
        from admin_portal import stations
        draft = stations.draft_of()
        row = next(r for r in draft["rows"] if r["id"] == self.bar.pk)
        with self.sending() as sender:
            row["active"] = False
            stations.save(draft)
            stations.save(draft)  # no change the second time
        self.assertEqual(self.types(sender), ["station.paused"])
        self.assertEqual(self.one(sender, "station.paused")["data"], {"station": {"code": "bar", "name": self.bar.name}})
        with self.sending() as sender:
            row["active"] = True
            stations.save(draft)
        self.assertEqual(self.types(sender), ["station.resumed"])

    # ----- cash -----

    def test_a_cash_drawer_opened_moved_and_closed_with_its_difference(self):
        self.client.force_login(self.boss)

        def post(**body):
            return self.client.post(reverse("cash_drawer"), json.dumps(body), content_type="application/json")

        with self.sending() as sender:
            self.assertEqual(post(action="open", amount="200.00").status_code, 200)
            post(action="open", amount="1")  # already open
        opened = self.one(sender, "drawer.opened")
        self.assertEqual((opened["data"]["drawer"]["opening"], opened["data"]["staff"]["name"], opened["subject"]["email"]),
                         (200.0, "Priya Shah", "boss@example.org"))
        with self.sending() as sender:
            post(action="move", amount="-20.00", reason="Milk run")
        moved = self.one(sender, "drawer.cash_moved")
        self.assertEqual((moved["data"]["amount"], moved["data"]["reason"]), (-20.0, "Milk run"))
        with self.sending() as sender:
            self.assertEqual(post(action="close", counted="178.00").status_code, 200)
        closed = self.one(sender, "drawer.closed")
        self.assertEqual({k: closed["data"]["drawer"][k] for k in ("expected", "counted", "difference", "cash_sales", "orders")},
                         {"expected": 180.0, "counted": 178.0, "difference": -2.0, "cash_sales": 0.0, "orders": 0})

    # ----- people -----

    def test_a_customer_makes_an_account(self):
        with self.sending() as sender, mock.patch("users.accounts.send", return_value=True):
            self.client.post(reverse("store_join"), {"first": "Ada", "last": "Ng", "email": "ada@example.com", "phone": "604 555 0181",
                                                     "password": "long-enough-1", "news": "1"})
            self.client.post(reverse("store_join"), {"first": "Ada", "last": "Ng", "email": "ada@example.com", "password": "long-enough-1"})
        joined = self.one(sender, "customer.registered")
        ada = User.objects.get(email="ada@example.com")
        self.assertEqual(joined["data"], {"customer": {"id": str(ada.pk), "name": "Ada Ng", "email": "ada@example.com", "phone": "6045550181",
                                                       "newsletter": True}})
        self.assertEqual(joined["subject"], {"type": "person", "id": str(ada.pk), "name": "Ada Ng", "email": "ada@example.com"})

    def test_staff_added_by_making_an_account_or_promoting_a_customer(self):
        from admin_portal import staff as staff_page
        customer = User.objects.create_user("ana", "ana@example.org", "x", first_name="Ana")
        with self.sending() as sender:
            new, _problem = staff_page.create({"first": "Sam", "last": "Lee", "email": "sam@example.org"})
            staff_page.promote(customer)
            staff_page.promote(self.dana)  # already staff
        added = self.every(sender, "staff.added")
        self.assertEqual([e["data"]["staff"]["email"] for e in added], ["sam@example.org", "ana@example.org"])
        self.assertEqual(added[0]["data"], {"staff": {"id": str(new.pk), "name": "Sam Lee", "email": "sam@example.org"}, "roles": ""})

    # ----- the schedule -----

    def shift(self, user, days=3, start=540, end=900, published=True):
        shift = Shift(user=user, role=self.barista, date=timezone.localdate() + datetime.timedelta(days=days), start=start, end=end, published=published)
        if published:
            from team import schedule
            shift.published_as = schedule.snapshot(shift)
        shift.save()
        return shift

    def test_publishing_tells_each_person_once(self):
        from team import schedule
        self.shift(self.dana, days=2, published=False)
        self.shift(self.dana, days=4, published=False)
        self.shift(self.alex, days=20, published=False)
        with self.sending() as sender, mock.patch("team.schedule._notify"):
            schedule.publish()
            schedule.publish()  # nothing left to publish
        told = {e["data"]["staff"]["email"]: e["data"] for e in self.every(sender, "schedule.published")}
        self.assertEqual(set(told), {"dana@example.org", "alex@example.org"})
        self.assertEqual((told["dana@example.org"]["shifts"], told["dana@example.org"]["next_shift_role"]), (2, "Barista"))
        self.assertRegex(told["dana@example.org"]["next_shift_at"], r"T09:00:00[+-]\d\d:00$")
        self.assertEqual(told["alex@example.org"], {"staff": {"id": str(self.alex.pk), "name": "Alex P", "email": "alex@example.org"}, "shifts": 0})

    def test_a_shift_given_away_and_taken(self):
        from team import swaps
        shift = self.shift(self.dana)
        with self.sending() as sender:
            swaps.give(shift, self.dana, "Dentist appointment")
        offered = self.one(sender, "shift.offered")
        self.assertEqual((offered["data"]["staff"]["email"], offered["data"]["note"], offered["data"]["shift"]["role"]),
                         ("dana@example.org", "Dentist appointment", "Barista"))
        self.assertEqual(offered["data"]["shift"]["id"], str(shift.pk))
        with self.sending() as sender, mock.patch("team.swaps._tell"):
            swaps.grab(self.alex, shift)
        taken = self.one(sender, "shift.taken")
        self.assertEqual((taken["data"]["staff"]["email"], taken["data"]["from"]), ("alex@example.org", {"name": "Dana Lee", "email": "dana@example.org"}))

    def test_a_swap_a_manager_approves(self):
        from team import swaps
        mine, theirs = self.shift(self.dana, days=3), self.shift(self.alex, days=5)
        with self.sending() as sender, mock.patch("team.swaps._tell"):
            offer = swaps.ask(mine, self.dana, self.alex, theirs)
            swaps.answer(offer, self.alex, True)
        self.assertEqual(self.types(sender), [])  # nothing has moved yet
        with self.sending() as sender, mock.patch("team.swaps._tell"):
            swaps.decide(offer, True, self.boss)
        swapped = self.one(sender, "shift.swapped")
        self.assertEqual((swapped["data"]["kind"], swapped["data"]["staff"]["email"], swapped["data"]["to"]),
                         ("swap", "dana@example.org", {"name": "Alex P", "email": "alex@example.org"}))
        declined = swaps.ask(self.shift(self.dana, days=7), self.dana, self.alex)
        swaps.answer(declined, self.alex, True)
        with self.sending() as sender, mock.patch("team.swaps._tell"):
            swaps.decide(declined, False, self.boss)
        self.assertEqual(self.types(sender), [])

    def test_time_off_and_availability_asked_and_decided(self):
        from team import requests
        first = timezone.localdate() + datetime.timedelta(days=10)
        with self.sending() as sender:
            off = requests.ask_time_off(self.dana, first, first + datetime.timedelta(days=2), category="Vacation", reason="Family trip")
            windows = {str(d): [[540, 900]] for d in range(7)}
            change = requests.ask_availability(self.dana, windows, first, reason="School")
        asked = self.one(sender, "time_off.requested")
        self.assertEqual(asked["data"], {"staff": {"id": str(self.dana.pk), "name": "Dana Lee", "email": "dana@example.org"},
                                         "request": {"id": str(off.pk), "reason": "Family trip", "first_day": first.isoformat(),
                                                     "last_day": (first + datetime.timedelta(days=2)).isoformat(), "all_day": True,
                                                     "category": "Vacation"}})
        self.assertEqual(self.one(sender, "availability.requested")["data"]["request"],
                         {"id": str(change.pk), "reason": "School", "effective": first.isoformat(), "until": ""})
        with self.sending() as sender, mock.patch("team.requests._tell"):
            requests.decide(off, True, self.boss, "Enjoy the trip.")
            requests.decide(change, False, self.boss)
        decided = self.one(sender, "time_off.decided")
        self.assertEqual((decided["data"]["decision"], decided["data"]["message"], decided["data"]["decided_by"]), ("approved", "Enjoy the trip.", "Priya Shah"))
        self.assertEqual(self.one(sender, "availability.decided")["data"]["decision"], "declined")

    # ----- the store -----

    def test_pausing_and_resuming_online_orders(self):
        from admin_portal import hours
        with self.sending() as sender:
            until = hours.pause(30)
        self.assertEqual(self.one(sender, "store.paused")["data"], {"until": timezone.localtime(until).isoformat(timespec="seconds")})
        with self.sending() as sender:
            self.assertTrue(hours.resume())
            self.assertFalse(hours.resume())  # wasn't paused any more
        self.assertEqual(self.types(sender), ["store.resumed"])

    def test_an_event_added_to_the_calendar(self):
        from events.models import Event
        day = timezone.localdate() + datetime.timedelta(days=30)
        with self.sending() as sender:
            closure = Event.objects.create(kind=Event.CLOSED, title="Christmas Day", start_date=day, end_date=day, all_day=True, delivery=True)
            party = Event.objects.create(kind=Event.OPEN, title="Late night", start_date=day, end_date=day, opens=1260, closes=1500)
        added = self.every(sender, "store_event.created")
        self.assertEqual(added[0]["data"], {"event": {"id": str(closure.pk), "title": "Christmas Day", "kind": "closed", "starts_on": day.isoformat(),
                                                      "ends_on": day.isoformat(), "all_day": True, "hours": "", "channels": "dine-in, pickup, delivery",
                                                      "description": ""}})
        self.assertEqual((added[1]["data"]["event"]["id"], added[1]["data"]["event"]["hours"], added[1]["data"]["event"]["channels"]),
                         (str(party.pk), "9 PM – 1 AM", "dine-in, pickup"))
        self.assertEqual(len(added), 2)
        with self.sending() as sender:
            closure.title = "Christmas"
            closure.save()  # edits aren't new events
        self.assertEqual(self.types(sender), [])

    def test_a_dongle_going_offline_and_coming_back(self):
        from asgiref.sync import async_to_sync
        from django.core.cache import cache

        from devices import pairing, socket
        from devices.models import Dongle
        from screens.models import Screen
        dongle = Dongle.objects.create(name="Menu board left", hardware_id="7F3K", token_hash="x" * 64, screen=Screen.objects.create(name="Menu board", slug="menu"))
        cache.delete(pairing.online_key(dongle.pk))
        with self.sending() as sender:
            async_to_sync(socket._online)(dongle.pk, "conn-1", save=True)
            async_to_sync(socket._online)(dongle.pk, "conn-1", status={"uptime": 5})  # its status every 30 s: still online
        self.assertEqual(self.types(sender), ["dongle.online"])
        self.assertEqual(self.one(sender, "dongle.online")["data"], {"dongle": {"id": str(dongle.pk), "name": "Menu board left", "screen": "Menu board"}})
        with self.sending() as sender:
            async_to_sync(socket._offline)(dongle.pk, "an-older-connection")  # a newer one took over: not offline
            async_to_sync(socket._offline)(dongle.pk, "conn-1")
        self.assertEqual(self.types(sender), ["dongle.offline"])

    # ----- the promise -----

    def test_no_event_can_break_what_caused_it(self):
        with mock.patch("switchboard.events.emit", side_effect=RuntimeError("boom")), self.assertLogs("switchboard.events", "ERROR"):
            order = create_order(lines=[self.dish(self.tea)], channel="store", initial_status=OrderStation.STATUS_APPROVED)
            order.set_station_status(["bar"], OrderStation.STATUS_DONE)
        self.assertTrue(order.all_stations_done())

    def test_without_a_connection_nothing_is_built_or_asked(self):
        SwitchboardConnection.objects.all().delete()
        with self.sending() as sender, mock.patch("switchboard.events._order") as built:
            order = create_order(lines=[self.dish(self.tea)], channel="web", initial_status=OrderStation.STATUS_PENDING)
            order.set_station_status(["bar"], OrderStation.STATUS_APPROVED)
            events.order_rejected(order, "No tea")
        built.assert_not_called()
        self.assertEqual(sender.calls, [])
        self.assertFalse(Delivery.objects.exists())


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
