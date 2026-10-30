"""Tests for misc_tools: email through listmonk (its API is faked: FakeListmonk)."""
import datetime
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

    def __init__(self, old=False, no_patch=False):
        self.templates, self.lists, self.subscribers, self.sent, self.calls = {}, {}, {}, [], []
        self.campaigns = {}
        self.old = old  # an older listmonk: transactional email only to subscribers
        self.no_patch = no_patch  # an older listmonk: no PATCH /subscribers/<id>, only PUT (which replaces the lists)
        # listmonk's settings (push/listmonk.py adds a messenger): passwords come back as dots, and one sent empty is kept
        self.settings = {"app.root_url": "https://mail.example.com", "smtp": [{"uuid": "s-1", "host": "smtp.example.com", "password": "hunter2"}],
                         "messengers": []}

    def by_email(self, email_):
        return next((s for s in self.subscribers.values() if s["email"].lower() == email_.lower()), None)

    def in_list(self, email_, list_id):
        sub = self.by_email(email_)
        return bool(sub) and any(l["id"] == list_id and l["subscription_status"] != "unsubscribed" for l in sub["lists"])

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
            return [{"id": i, "name": n, "subscriber_count": 3} for i, n in self.lists.items()]
        if path == "/lists":
            self.lists[len(self.lists) + 1] = payload["name"]
            return {"id": len(self.lists)}
        if path.startswith("/subscribers?"):
            from urllib.parse import unquote
            email_ = unquote(path).split("LOWER('", 1)[-1].split("')", 1)[0]
            found = self.by_email(email_)
            return [found] if found else []
        if path == "/subscribers" and method == "POST":
            new = len(self.subscribers) + 1
            self.subscribers[new] = {"id": new, **payload, "lists": [{"id": i, "subscription_status": "confirmed"} for i in payload["lists"]]}
            return {"id": new}
        if path == "/subscribers/lists":
            for sid in payload["ids"]:
                sub = self.subscribers[sid]
                for list_id in payload["target_list_ids"]:
                    sub["lists"] = [l for l in sub["lists"] if l["id"] != list_id]
                    sub["lists"].append({"id": list_id, "subscription_status": "unsubscribed" if payload["action"] == "unsubscribe" else "confirmed"})
            return True
        if path.startswith("/subscribers/") and method == "PATCH":
            if self.no_patch:
                raise listmonk.ListmonkError("listmonk returned 404: 404 unknown endpoint", 404)
            self.subscribers[int(path.rsplit("/", 1)[1])].update(payload)
            return True
        if path.startswith("/subscribers/") and method == "PUT" and path.rsplit("/", 1)[1].isdigit():
            sub = self.subscribers[int(path.rsplit("/", 1)[1])]
            had = {row["id"]: row for row in sub["lists"]}  # listmonk keeps a subscription's status; lists left out go
            sub.update({k: v for k, v in payload.items() if k != "lists"})
            sub["lists"] = [had.get(i, {"id": i, "subscription_status": "unconfirmed"}) for i in payload["lists"]]
            return True
        if path == "/campaigns" and method == "POST":
            new = len(self.campaigns) + 1
            self.campaigns[new] = {**payload, "status": "draft"}
            return {"id": new}
        if path.startswith("/campaigns/") and path.endswith("/status"):
            self.campaigns[int(path.split("/")[2])]["status"] = payload["status"]
            return True
        if path == "/settings" and method == "GET":
            import copy
            shown = copy.deepcopy(self.settings)
            for row in shown["smtp"] + shown["messengers"]:
                row["password"] = "•" * len(row.get("password", ""))
            return shown
        if path == "/settings" and method == "PUT":
            for kind in ("smtp", "messengers"):
                for row in payload[kind]:
                    if not row.get("password"):
                        row["password"] = next((r["password"] for r in self.settings[kind] if r.get("uuid") and r.get("uuid") == row.get("uuid")), "")
            self.settings = payload
            return True
        if path == "/tx":
            if self.old and not all(self.by_email(e) for e in payload["subscriber_emails"]):
                raise listmonk.ListmonkError("listmonk returned 400: subscriber not found", 400)
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
        made = sum(len(listmonk.template_languages(k)) for k in KINDS) + langs  # every email, and the newsletter template
        self.assertEqual(done["made"], made)
        self.assertEqual(EmailTemplate.objects.count(), made)
        self.assertEqual(list(self.fake.lists.values()), ["My restaurant users", "My restaurant customers"])
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

    def test_everyone_in_the_users_list_and_menu_email_people_in_the_customers_list(self):
        from admin_portal.models import StoreSettings
        ben = User.objects.create_user("ben", email="ben@example.com", is_staff=True)
        ben.customer_profile.newsletter = True
        ben.customer_profile.save()
        User.objects.create_user("noemail")
        self.assertEqual(listmonk.sync_all(), 2)
        store = StoreSettings.load()
        users, customers = store.listmonk_users_list, store.listmonk_list
        self.assertEqual(sorted(self.fake.lists.values()), ["My restaurant customers", "My restaurant users"])
        self.assertTrue(self.fake.in_list("ana@example.com", users) and self.fake.in_list("ben@example.com", users))
        self.assertEqual((self.fake.in_list("ana@example.com", customers), self.fake.in_list("ben@example.com", customers)), (False, True))
        self.assertEqual(self.fake.by_email("ben@example.com")["attribs"]["staff"], True)
        ana = self.ana.customer_profile
        ana.newsletter = True
        ana.save()
        listmonk.sync_user(self.ana)
        self.assertTrue(self.fake.in_list("ana@example.com", customers))
        ana.newsletter = False
        ana.save()
        listmonk.sync_user(self.ana)
        self.assertEqual((self.fake.in_list("ana@example.com", customers), self.fake.in_list("ana@example.com", users)), (False, True))
        self.assertEqual(listmonk.sync_all(), 2)  # again: nothing doubles up
        self.assertEqual(len(self.fake.subscribers), 2)

    def test_an_older_listmonk_without_patch_still_gets_names_and_attributes(self):
        from django.core.cache import cache
        from admin_portal.models import StoreSettings
        from push.models import PushSubscription
        cache.delete(listmonk.NO_PATCH)
        self.fake.no_patch = True  # listmonk before PATCH /subscribers/<id>: "404 unknown endpoint"
        listmonk.sync_user(self.ana)
        customers = StoreSettings.load().listmonk_list
        sub = self.fake.by_email("ana@example.com")
        sub["lists"].append({"id": customers, "subscription_status": "unsubscribed"})  # she left the newsletter in listmonk
        phone = PushSubscription.objects.create(app="store", user=self.ana, news_updates=True, endpoint="https://push.example.com/1", p256dh="k", auth="a")
        self.ana.first_name = "Anastasia"
        self.ana.save()
        listmonk.sync_user(self.ana)
        sub = self.fake.by_email("ana@example.com")
        self.assertEqual((sub["name"], sub["attribs"]["pavilion_push"]), ("Anastasia", [phone.pk]))  # through PUT
        self.assertTrue(self.fake.in_list("ana@example.com", StoreSettings.load().listmonk_users_list))  # still in the users list
        self.assertFalse(self.fake.in_list("ana@example.com", customers))  # and still out of the newsletter
        self.assertTrue(cache.get(listmonk.NO_PATCH))  # later syncs go straight to PUT
        cache.delete(listmonk.NO_PATCH)

    def test_event_emails_are_campaign_templates_with_every_language(self):
        listmonk.install()
        for key in ("event_open", "event_closed"):
            row = EmailTemplate.objects.get(kind=key)
            self.assertEqual(row.language, EmailTemplate.ALL)
            made = self.fake.templates[row.template_id]
            self.assertEqual(made["type"], "campaign")
            self.assertIn('{{ template "content" . }}', made["body"])
            self.assertIn("{{ UnsubscribeURL }}", made["body"])
            self.assertIn('{{ if eq (or .Subscriber.Attribs.language "en") "uk" }}', made["body"])
        opened = self.fake.templates[EmailTemplate.objects.get(kind="event_open").template_id]["body"]
        self.assertIn("Extra hours", opened)
        self.assertIn("Додаткові години", opened)

    def _event(self, **kw):
        from django.utils import timezone
        from events.models import Event
        day = timezone.localdate() + datetime.timedelta(days=5)
        values = dict(kind=Event.OPEN, title="Trivia night", description="Six rounds.\nPrizes!", start_date=day, end_date=day,
                      opens=1140, closes=1320, dine_in=True, pickup=True, delivery=False)
        return Event.objects.create(**{**values, **kw})

    def test_a_new_event_goes_out_as_a_campaign_to_the_customers_and_the_lists_picked(self):
        from django.test import RequestFactory
        from admin_portal.models import StoreSettings
        from events.announce import announce_later
        from events.models import Event
        lists = listmonk.ensure_lists()
        culinary = self.fake("POST", "/lists", {"name": "Culinary events"})["id"]
        EmailSwitch.objects.create(kind="event_open", lists=[lists["users"], culinary])  # the users list never gets campaigns
        request = RequestFactory().get("/", HTTP_HOST="localhost")
        with mock.patch("misc_tools.listmonk._background", lambda job: job()), self.captureOnCommitCallbacks(execute=True):
            jobs = announce_later([self._event(), self._event(title="Long ago", start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2020, 1, 1))], request)
        self.assertEqual([j["title"] for j in jobs], ["Trivia night"])  # only events that haven't ended
        (sent,) = self.fake.campaigns.values()
        self.assertEqual((sent["lists"], sent["status"]), ([StoreSettings.load().listmonk_list, culinary], "running"))
        self.assertEqual(sent["template_id"], EmailTemplate.objects.get(kind="event_open").template_id)  # installed on first send
        self.assertIn("Trivia night at My restaurant", sent["subject"])
        self.assertIn("{{ else }}", sent["subject"])  # every language
        self.assertIn("http://localhost/store/menu@TrackLink", sent["body"])  # listmonk counts the clicks
        self.assertIn("Six rounds.<br>Prizes!", sent["body"])
        self.assertIn("7 PM – 10 PM", sent["body"])

        closed = self._event(kind=Event.CLOSED, title="Closed for {{ repairs }}", all_day=True, delivery=True)
        with mock.patch("misc_tools.listmonk._background", lambda job: job()), self.captureOnCommitCallbacks(execute=True):
            announce_later([closed], request)
        sent = self.fake.campaigns[2]
        self.assertEqual(sent["lists"], [StoreSettings.load().listmonk_list])
        self.assertIn("Closed for { { repairs } }", sent["subject"])  # never read as listmonk's code
        self.assertIn("Closed all day", sent["body"])
        self.assertIn("http://localhost/store/", sent["body"])

        EmailSwitch.objects.filter(kind="event_open").update(enabled=False)
        self.assertEqual(announce_later([self._event()], request), [])
        self.assertIsNone(email.send_campaign("event_open", "x", "s", "b"))

    def test_an_older_listmonk_gets_unknown_addresses_added_first(self):
        self.fake.old = True
        listmonk.install()
        self.assertTrue(email.send("gift_card", "friend@example.com", {"name": "Friend"}))
        self.assertEqual(self.fake.by_email("friend@example.com")["lists"], [])  # a subscriber, in no list
        self.assertEqual(self.fake.sent[-1]["subscriber_emails"], ["friend@example.com"])


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
        with mock.patch("misc_tools.listmonk._background", lambda job: job()), self.captureOnCommitCallbacks(execute=True):
            x = self.client.post(self.url, {"op": "connect", "url": "https://mail.example.com", "user": "pavilion", "token": "k"}).context["x"]
        self.assertTrue(x["connected"])
        self.assertTrue(self.fake.in_list("owner@example.com", StoreSettings.load().listmonk_users_list))  # everyone synced on connecting
        self.assertEqual(self.client.get(self.url).context["x"]["sync"]["state"], "done")
        self.assertEqual(StoreSettings.load().listmonk_token, "k")
        self.assertEqual(x["missing"], sum(len(listmonk.template_languages(k)) for k in KINDS))
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
        culinary = self.fake("POST", "/lists", {"name": "Culinary events"})["id"]
        cache.clear()
        x = self.client.post(self.url, {"op": "cat:events"}).context["x"]
        self.assertEqual([e["k"].key for e in x["emails"]], ["event_open", "event_closed"])
        lists = x["emails"][0]["lists"]
        self.assertEqual(([r["name"] for r in lists["rows"]], lists["customers"]["name"]), (["Culinary events"], "My restaurant customers"))
        self.assertEqual([r["lang"] for r in x["emails"][0]["rows"]], [EmailTemplate.ALL])
        x = self.client.post(self.url, {"op": f"list:event_open:{culinary}"}).context["x"]
        self.assertTrue(x["emails"][0]["lists"]["rows"][0]["on"] and x["dirty"])
        self.client.post(self.url, {"op": "save"})
        self.assertEqual(EmailSwitch.objects.get(kind="event_open").lists, [culinary])
        self.client.post(self.url, {"op": "disconnect"})
        self.assertFalse(self.client.get(self.url).context["x"]["connected"])
