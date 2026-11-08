import asyncio
import json
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pavilion.realtime import get_broker
from screens.models import Screen
from users.testing import crew

from . import pairing
from .models import Dongle, DonglePairingCode
from .socket import dongle_socket

DEVICE = {"hardware_id": "7f3k", "model": "Raspberry Pi 4 Model B", "version": "0.1.0", "resolution": "1920x1080",
          "network": {"ssid": "Juniper-Staff", "signal": 88, "ip": "192.168.1.42", "wired": False}}


class PairingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser("owner", password="x")

    def post(self, body):
        return self.client.post(reverse("dongle_pair"), json.dumps(body), content_type="application/json")

    def test_a_good_code_pairs_once(self):
        code = pairing.new_code(self.owner)
        self.assertEqual(len(code.code), 6)
        self.assertTrue(code.live)
        answer = self.post({"code": code.code, **DEVICE})
        self.assertEqual(answer.status_code, 200)
        data = answer.json()
        dongle = Dongle.objects.get()
        self.assertEqual(data["dongle"], {"id": dongle.pk, "name": "New visual dongle"})
        self.assertTrue(data["token"].startswith(f"{dongle.pk}."))
        self.assertNotIn(data["token"].split(".", 1)[1], dongle.token_hash)  # only its hash is kept
        self.assertEqual(pairing.check_token(data["token"]), dongle)
        self.assertIsNone(pairing.check_token(f"{dongle.pk}.wrong"))
        self.assertIsNone(pairing.check_token("nonsense"))
        self.assertEqual(dongle.paired_by, self.owner)
        self.assertEqual(dongle.info["network"]["ssid"], "Juniper-Staff")
        code.refresh_from_db()
        self.assertEqual(code.dongle, dongle)
        again = self.post({"code": code.code, **DEVICE})
        self.assertEqual((again.status_code, again.json()["error"]), (410, "used"))

    def test_codes_that_dont_work(self):
        self.assertEqual(self.post({"code": "000000", **DEVICE}).json()["error"], "bad_code")
        old = pairing.new_code(self.owner)
        DonglePairingCode.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.post({"code": old.code, **DEVICE}).json()["error"], "expired")
        self.assertEqual(self.post({"code": "12"}).status_code, 400)
        self.assertEqual(self.client.post(reverse("dongle_pair"), "not json", content_type="application/json").status_code, 400)
        self.assertFalse(Dongle.objects.exists())

    def test_codes_last_fifteen_minutes(self):
        code = pairing.new_code(self.owner)
        self.assertAlmostEqual((code.expires_at - code.created_at).total_seconds(), 15 * 60, delta=1)

    def test_tries_are_limited(self):
        from django.core.cache import cache
        cache.clear()
        with self.settings(THROTTLE=True):
            answers = [self.post({"code": "000000", **DEVICE}).status_code for _n in range(12)]
        self.assertEqual(answers[:10], [404] * 10)
        self.assertEqual(answers[10:], [429, 429])

    def test_status_is_cleaned(self):
        info = pairing.clean_info({"model": "x" * 500, "network": {"signal": "lots", "ssid": 5}, "uptime": -3, "evil": 1})
        self.assertEqual(len(info["model"]), 80)
        self.assertEqual(info["network"], {"ssid": "5", "signal": 0, "ip": "", "wired": False})
        self.assertEqual(info["uptime"], 0)
        self.assertNotIn("evil", info)


class ScreenAccessTests(TestCase):
    """A dongle's browser sees its own screen through the launch link's cookie, nobody else's."""

    @classmethod
    def setUpTestData(cls):
        cls.private = Screen.objects.create(name="Kitchen board", slug="kitchen-board", public=False)
        cls.other = Screen.objects.create(name="Office board", slug="office-board", public=False)
        cls.dongle, cls.token = pairing.pair(pairing.new_code().code, DEVICE)
        Dongle.objects.filter(pk=cls.dongle.pk).update(screen=cls.private)
        cls.dongle.refresh_from_db()

    def test_launch_link_opens_the_screen(self):
        url = reverse("screen", args=[self.private.slug])
        self.assertEqual(self.client.get(url).status_code, 302)  # to sign in
        launch = pairing.config(self.dongle)["screen"]["url"]
        opened = self.client.get(launch)
        self.assertRedirects(opened, url, fetch_redirect_response=False)
        self.assertIn(pairing.COOKIE, opened.cookies)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse("screen", args=[self.other.slug])).status_code, 302)

    def test_links_die_with_the_pairing(self):
        launch = pairing.config(self.dongle)["screen"]["url"]
        self.assertEqual(self.client.get(launch[:-3] + "abc").status_code, 403)  # tampered
        self.client.get(launch)
        Dongle.objects.filter(pk=self.dongle.pk).update(token_hash=pairing.hash_secret("re-paired"))
        self.assertEqual(self.client.get(reverse("screen", args=[self.private.slug])).status_code, 302)
        self.assertEqual(self.client.get(launch).status_code, 403)

    def test_a_new_screen_is_a_new_address(self):
        before = pairing.launch_path(self.dongle)
        self.dongle.screen = self.other
        self.assertNotEqual(pairing.launch_path(self.dongle), before)
        self.dongle.screen = self.private
        self.assertEqual(pairing.launch_path(self.dongle), before)  # same screen: same address (no reload)


class WebAddressTests(TestCase):
    """A dongle can show any web address instead of one of Pavilion's screens."""

    @classmethod
    def setUpTestData(cls):
        cls.screen = Screen.objects.create(name="Kitchen board", slug="kitchen-board", public=False)
        cls.dongle, _token = pairing.pair(pairing.new_code().code, DEVICE)

    def test_addresses_that_work(self):
        self.assertEqual(pairing.clean_url(""), "")
        self.assertEqual(pairing.clean_url(" https://example.com/menu?a=1 "), "https://example.com/menu?a=1")
        self.assertEqual(pairing.clean_url("http://192.168.1.50:8080/"), "http://192.168.1.50:8080/")
        self.assertEqual(pairing.clean_url("example.com/specials"), "https://example.com/specials")
        self.assertEqual(pairing.clean_url("/store/"), "/store/")
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "ftp://example.com", "//evil.com", "https://exa mple.com",
                    "not a url", "data:text/html,hi"):
            self.assertIsNone(pairing.clean_url(bad), bad)

    def test_the_address_wins_over_the_screen(self):
        self.dongle.screen, self.dongle.url = self.screen, "https://www.example.com/tv"
        self.assertEqual(pairing.config(self.dongle)["screen"], {"name": "example.com", "url": "https://www.example.com/tv"})
        self.assertEqual(self.dongle.showing, "example.com")
        self.dongle.url = "/store/"
        self.assertEqual(pairing.config(self.dongle)["screen"], {"name": "/store/", "url": "/store/"})
        self.dongle.url = ""
        self.assertTrue(pairing.config(self.dongle)["screen"]["url"].startswith("/devices/dongles/open/"))


class SocketTests(TestCase):
    """The WebSocket at /ws/dongle, driven by hand."""

    @classmethod
    def setUpTestData(cls):
        cls.screen = Screen.objects.create(name="Specials", slug="specials", public=True)
        cls.dongle, cls.token = pairing.pair(pairing.new_code().code, DEVICE)

    async def connect(self, token):
        inbox, sent = asyncio.Queue(), asyncio.Queue()
        await inbox.put({"type": "websocket.connect"})
        scope = {"type": "websocket", "path": "/ws/dongle", "headers": [(b"authorization", f"Bearer {token}".encode())]}
        task = asyncio.ensure_future(dongle_socket(scope, inbox.get, sent.put))
        return inbox, sent, task

    async def next(self, sent):
        return await asyncio.wait_for(sent.get(), 3)

    async def test_an_unknown_token_is_told_its_unpaired(self):
        _inbox, sent, task = await self.connect(f"{self.dongle.pk}.wrong")
        self.assertEqual((await self.next(sent))["type"], "websocket.accept")
        self.assertEqual(await self.next(sent), {"type": "websocket.close", "code": 4401})
        await asyncio.wait_for(task, 3)

    async def test_config_status_commands_and_unpairing(self):
        inbox, sent, task = await self.connect(self.token)
        self.assertEqual((await self.next(sent))["type"], "websocket.accept")
        config = json.loads((await self.next(sent))["text"])
        self.assertEqual((config["type"], config["name"], config["screen"]), ("config", "New visual dongle", None))
        self.assertTrue(await sync_to_async(pairing.is_online)(self.dongle.pk))

        await inbox.put({"type": "websocket.receive", "text": json.dumps(
            {"type": "status", **DEVICE, "network": {"ssid": "Juniper-Guest", "signal": 40, "ip": "192.168.1.77"}, "showing": "idle"})})
        for _n in range(50):
            info = (await Dongle.objects.aget(pk=self.dongle.pk)).info
            if info["network"]["ssid"] == "Juniper-Guest":
                break
            await asyncio.sleep(0.02)
        self.assertEqual(info["network"]["ip"], "192.168.1.77")
        self.assertEqual(info["showing"], "idle")

        # A screen picked in the admin: the socket sends the new config
        await Dongle.objects.filter(pk=self.dongle.pk).aupdate(screen=self.screen, name="Window TV")
        get_broker().publish(pairing.topic(self.dongle.pk), json.dumps({"type": "config"}))
        config = json.loads((await self.next(sent))["text"])
        self.assertEqual(config["name"], "Window TV")
        self.assertEqual(config["screen"]["name"], "Specials")
        self.assertTrue(config["screen"]["url"].startswith("/devices/dongles/open/"))

        get_broker().publish(pairing.topic(self.dongle.pk), json.dumps({"type": "command", "command": "identify", "id": "c1"}))
        self.assertEqual(json.loads((await self.next(sent))["text"])["command"], "identify")

        get_broker().publish(pairing.topic(self.dongle.pk), json.dumps({"type": "unpaired"}))
        self.assertEqual(json.loads((await self.next(sent))["text"]), {"type": "unpaired"})
        self.assertEqual(await self.next(sent), {"type": "websocket.close", "code": 4401})
        await asyncio.wait_for(task, 3)
        self.assertFalse(await sync_to_async(pairing.is_online)(self.dongle.pk))


class AdminDevicesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser("owner", password="x", first_name="Priya", last_name="Shah")
        cls.screen = Screen.objects.create(name="Menu board, left wall", slug="left-wall", public=True)
        cls.dongle, _token = pairing.pair(pairing.new_code().code, DEVICE)

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = reverse("admin_devices")

    def test_add_a_dongle_then_pick_its_screen(self):
        self.client.post(self.url, {"op": "dongle_add"})
        code = DonglePairingCode.objects.filter(used_at__isnull=True).get()
        page = self.client.get(self.url)
        self.assertContains(page, "Waiting for a dongle to use this code")
        self.assertContains(page, f"<span>{code.code[0]}</span>")
        self.client.post(self.url, {"op": "dongle_newcode"})  # the old one stops working
        code.refresh_from_db()
        self.assertFalse(code.live)
        fresh = DonglePairingCode.objects.filter(used_at__isnull=True).latest("created_at")
        dongle, _token = pairing.pair(fresh.code, {**DEVICE, "hardware_id": "Q8MZ"})
        page = self.client.get(self.url)
        self.assertContains(page, "Your new dongle is paired")
        self.assertContains(page, "New visual dongle is paired")
        self.assertNotContains(page, "Name it after where it hangs")  # renamed later, in its settings
        self.client.post(self.url, {"op": "dongle_finish", "screen": self.screen.pk})
        dongle.refresh_from_db()
        self.assertEqual((dongle.name, dongle.screen), ("New visual dongle", self.screen))
        self.assertNotContains(self.client.get(self.url), "Your new dongle is paired")

    def test_what_it_shows_and_commands(self):
        page = self.client.get(self.url, {"dongle": self.dongle.pk})
        self.assertContains(page, "when it was showing nothing.")
        self.assertContains(page, "Last picture unknown")  # offline
        self.client.post(self.url, {"op": "dongle_save", "dongle": self.dongle.pk, "name": "Left wall TV",
                                    "screen": self.screen.pk, "orientation": "portrait"})
        self.dongle.refresh_from_db()
        self.assertEqual((self.dongle.name, self.dongle.screen, self.dongle.orientation), ("Left wall TV", self.screen, "portrait"))
        offline = self.client.post(self.url, {"op": "dongle_identify", "dongle": self.dongle.pk}, follow=True)
        self.assertContains(offline, "is offline")
        from django.core.cache import cache
        cache.set(pairing.online_key(self.dongle.pk), "conn", 60)
        sent = []
        from unittest import mock
        with mock.patch.object(get_broker(), "publish", side_effect=lambda topic, message: sent.append((topic, json.loads(message)))):
            with self.captureOnCommitCallbacks(execute=True):
                done = self.client.post(self.url, {"op": "dongle_restart", "dongle": self.dongle.pk}, follow=True)
        self.assertContains(done, "is restarting")
        self.assertIn((pairing.topic(self.dongle.pk), "restart"), [(t, m.get("command")) for t, m in sent])

    def test_show_a_web_address(self):
        here = {"op": "dongle_save", "dongle": self.dongle.pk, "name": "Window TV", "orientation": "landscape"}
        self.client.post(self.url, {**here, "screen": "url", "url": "https://example.com/specials"})
        self.dongle.refresh_from_db()
        self.assertEqual((self.dongle.url, self.dongle.screen), ("https://example.com/specials", None))
        page = self.client.get(self.url, {"dongle": self.dongle.pk})
        self.assertContains(page, '<option value="url" selected>')
        self.assertContains(page, 'value="https://example.com/specials"')
        bad = self.client.post(self.url, {**here, "name": "Changed", "screen": "url", "url": "javascript:alert(1)"}, follow=True)
        self.assertContains(bad, "Type a web address")
        self.dongle.refresh_from_db()
        self.assertEqual((self.dongle.name, self.dongle.url), ("Window TV", "https://example.com/specials"))  # nothing saved
        self.client.post(self.url, {**here, "screen": self.screen.pk, "url": "https://example.com/specials"})
        self.dongle.refresh_from_db()
        self.assertEqual((self.dongle.url, self.dongle.screen), ("", self.screen))  # a screen clears the address

    def test_pick_a_web_address_when_pairing(self):
        self.client.post(self.url, {"op": "dongle_add"})
        dongle, _token = pairing.pair(DonglePairingCode.objects.filter(used_at__isnull=True).get().code, {**DEVICE, "hardware_id": "H2RW"})
        self.client.post(self.url, {"op": "dongle_finish", "screen": "url", "url": "nope"})
        self.assertContains(self.client.get(self.url), "Your new dongle is paired")  # still open to fix it
        self.client.post(self.url, {"op": "dongle_finish", "screen": "url", "url": "https://example.com"})
        dongle.refresh_from_db()
        self.assertEqual(dongle.url, "https://example.com")

    def test_unpair_asks_first(self):
        self.client.post(self.url, {"op": "dongle_askunpair", "dongle": self.dongle.pk})
        self.assertContains(self.client.get(self.url, {"dongle": self.dongle.pk}), f"Unpair {self.dongle.name}?")
        self.client.post(self.url, {"op": "dongle_unpair", "dongle": self.dongle.pk})
        self.assertFalse(Dongle.objects.filter(pk=self.dongle.pk).exists())

    def test_viewers_cant_change_dongles(self):
        viewer = crew(User.objects.create_user("viewer", password="x", is_staff=True), "app.admin", "admin.devices.view")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.url, {"dongle": self.dongle.pk}).status_code, 200)
        self.client.post(self.url, {"op": "dongle_save", "dongle": self.dongle.pk, "name": "Nope"})
        self.client.post(self.url, {"op": "dongle_unpair", "dongle": self.dongle.pk})
        self.client.post(self.url, {"op": "dongle_add"})
        self.dongle.refresh_from_db()
        self.assertNotEqual(self.dongle.name, "Nope")
        self.assertEqual(DonglePairingCode.objects.filter(used_at__isnull=True).count(), 0)
