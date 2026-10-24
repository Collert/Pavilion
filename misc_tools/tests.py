"""Tests for misc_tools: email through listmonk (its API is faked: FakeListmonk)."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from . import email, listmonk
from .emails import KINDS
from .models import EmailSwitch, EmailTemplate

LISTMONK = dict(LISTMONK_URL="https://lists.example.com", LISTMONK_API_USER="pavilion", LISTMONK_API_TOKEN="secret",
                LISTMONK_FROM_EMAIL="Corner Kitchen <hello@example.com>")


class FakeListmonk:
    """listmonk's API in memory: templates, lists, subscribers and the transactional messages sent."""

    def __init__(self):
        self.templates, self.lists, self.subscribers, self.sent, self.calls = {}, {}, {}, [], []

    def __call__(self, method, path, payload=None, cfg=None):
        self.calls.append((method, path))
        if path.startswith("/templates?"):
            return [{"id": i, "name": t["name"], "type": t["type"]} for i, t in self.templates.items()]
        if path == "/templates" and method == "POST":
            new = len(self.templates) + 1
            self.templates[new] = payload
            return {"id": new}
        if path.startswith("/templates/") and method == "PUT":
            self.templates[int(path.rsplit("/", 1)[1])] = payload
            return True
        if path.startswith("/lists?"):
            return [{"id": i, "name": n} for i, n in self.lists.items()]
        if path == "/lists":
            self.lists[len(self.lists) + 1] = payload["name"]
            return {"id": len(self.lists)}
        if path.startswith("/subscribers?"):
            email_ = path.split("LOWER%28%27", 1)[-1].split("%27", 1)[0] if "LOWER%28%27" in path else ""
            return [s for s in self.subscribers.values() if s["email"].lower() == email_.lower()]
        if path == "/subscribers" and method == "POST":
            new = len(self.subscribers) + 1
            self.subscribers[new] = {"id": new, **payload}
            return {"id": new}
        if path == "/tx":
            self.sent.append(payload)
            return True
        return True


@override_settings(**LISTMONK)
class EmailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeListmonk()
        patcher = mock.patch("misc_tools.listmonk.api", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ana = User.objects.create_user("ana", email="ana@example.com", first_name="Ana")

    @override_settings(LISTMONK_URL="")
    def test_without_listmonk_emails_are_only_logged(self):
        with self.assertLogs("misc_tools.email", "INFO"):
            self.assertFalse(email.send("receipt", "a@example.com", {"number": 1}))
        self.assertEqual(self.fake.calls, [])

    def test_installing_writes_each_email_in_each_language(self):
        done = listmonk.install()
        langs = len(listmonk.languages())
        self.assertEqual(done["made"], (len(KINDS) + 1) * langs)  # every email, and the newsletter template
        self.assertEqual(EmailTemplate.objects.count(), (len(KINDS) + 1) * langs)
        self.assertEqual(list(self.fake.lists.values()), ["My restaurant customers"])
        tid = EmailTemplate.objects.get(kind="password_reset", language="uk").template_id
        made = self.fake.templates[tid]
        self.assertEqual(made["type"], "tx")
        self.assertIn("{{ .Tx.Data.link }}", made["body"])
        self.assertIn("{{ .Tx.Data.name }}", made["body"])
        self.assertIn('lang="uk"', made["body"])
        receipt = self.fake.templates[EmailTemplate.objects.get(kind="receipt", language="en").template_id]
        self.assertEqual(receipt["subject"], "Your receipt from My restaurant · order #{{ .Tx.Data.number }}")
        self.assertIn("{{ range .Tx.Data.items }}", receipt["body"])
        news = self.fake.templates[EmailTemplate.objects.get(kind="newsletter", language="en").template_id]
        self.assertEqual(news["type"], "campaign")
        self.assertIn('{{ template "content" . }}', news["body"])
        self.assertEqual(listmonk.install()["made"], 0)  # only what's missing

    def test_sending_in_their_language(self):
        from users.models import set_language
        listmonk.install()
        set_language(self.ana, "uk")
        self.assertTrue(email.send("password_reset", "ana@example.com", {"name": "Ana", "link": "https://x"}))
        sent = self.fake.sent[-1]
        self.assertEqual(sent["template_id"], EmailTemplate.objects.get(kind="password_reset", language="uk").template_id)
        self.assertEqual((sent["data"]["store"], sent["from_email"]), ("My restaurant", "Corner Kitchen <hello@example.com>"))
        email.send("password_reset", "nobody@example.com", {"name": "X", "link": "y"}, fallback="zh-HK")
        self.assertEqual(self.fake.sent[-1]["template_id"], EmailTemplate.objects.get(kind="password_reset", language="zh-HK").template_id)
        EmailTemplate.objects.filter(kind="password_reset", language="uk").delete()
        email.send("password_reset", "ana@example.com", {"name": "Ana", "link": "y"})  # falls back to the default language
        self.assertEqual(self.fake.sent[-1]["template_id"], EmailTemplate.objects.get(kind="password_reset", language="en").template_id)
        EmailSwitch.objects.create(kind="order_ready", enabled=False)
        self.assertFalse(email.send("order_ready", "ana@example.com", {"number": 3}))

    def test_a_missing_template_is_made_on_first_send(self):
        email.send("test", "a@example.com", {"name": "A"})
        self.assertEqual(EmailTemplate.objects.filter(kind="test").count(), len(listmonk.languages()))
        self.assertEqual(len(self.fake.sent), 1)

    def test_newsletter_list(self):
        listmonk.install()
        profile = self.ana.customer_profile
        profile.newsletter = True
        profile.save()
        listmonk.sync_customer(self.ana)
        sub = next(iter(self.fake.subscribers.values()))
        self.assertEqual((sub["email"], sub["lists"], sub["attribs"]["language"]), ("ana@example.com", [1], "en"))


@override_settings(LISTMONK_URL="", LISTMONK_API_USER="", LISTMONK_API_TOKEN="")
class EmailPageTests(TestCase):
    """The admin's Email page (admin_portal/email_settings.py)."""
    def setUp(self):
        cache.clear()
        self.fake = FakeListmonk()
        patcher = mock.patch("misc_tools.listmonk.api", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "x", first_name="Priya")
        self.client.force_login(self.owner)
        self.url = reverse("admin_email")

    def test_connect_install_pick_and_reset(self):
        from admin_portal.models import StoreSettings
        x = self.client.get(self.url).context["x"]
        self.assertFalse(x["connected"])
        x = self.client.post(self.url, {"op": "connect", "url": "https://mail.example.com", "user": "pavilion", "token": "k"}).context["x"]
        self.assertTrue(x["connected"])
        self.assertEqual(StoreSettings.load().listmonk_token, "k")
        self.assertEqual(x["missing"], len(KINDS) * 3)
        x = self.client.post(self.url, {"op": "install"}).context["x"]
        self.assertEqual(x["missing"], 0)
        x = self.client.post(self.url, {"op": "cat:orders"}).context["x"]
        self.assertEqual([e["k"].key for e in x["emails"]], ["receipt", "order_ready", "refund_issued"])
        mine = self.fake("POST", "/templates", {"name": "My own receipt", "type": "tx", "subject": "s", "body": "b"})["id"]
        cache.clear()
        self.client.post(self.url, {"op": "flip:order_ready", "tpl_receipt_uk": str(mine)})
        self.client.post(self.url, {"op": "save"})
        self.assertFalse(EmailSwitch.objects.get(kind="order_ready").enabled)
        row = EmailTemplate.objects.get(kind="receipt", language="uk")
        self.assertEqual((row.template_id, row.managed), (mine, False))
        self.client.post(self.url, {"op": "reset:receipt"})
        row = EmailTemplate.objects.get(kind="receipt", language="uk")
        self.assertTrue(row.managed)
        self.assertNotEqual(row.template_id, mine)
        self.assertEqual(self.fake.templates[mine]["name"], "My own receipt")  # theirs is left alone
        self.client.post(self.url, {"op": "test"})
        self.assertEqual(self.fake.sent[-1]["subscriber_emails"], ["owner@example.com"])
        self.client.post(self.url, {"op": "disconnect"})
        self.assertFalse(self.client.get(self.url).context["x"]["connected"])
