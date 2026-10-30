import base64
import json
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from py_vapid import Vapid
from pywebpush import WebPushException

from push import send, subscriptions, vapid
from push.models import PushSubscription, VapidKeys


def browser_subscription(n=1):
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/device-{n}", "keys": {"p256dh": f"key-{n}", "auth": f"auth-{n}"}}


def refused(status):
    response = mock.Mock(status_code=status, text="no")
    return WebPushException("refused", response=response)


class VapidTests(TestCase):
    def setUp(self):
        cache.delete(vapid.CACHE_KEY)

    def test_a_key_pair_is_made_once_and_kept(self):
        key = vapid.public_key()
        self.assertEqual(len(key), 87)  # 65 bytes, base64url without padding
        cache.delete(vapid.CACHE_KEY)
        self.assertEqual(vapid.public_key(), key)
        self.assertEqual(VapidKeys.objects.count(), 1)
        self.assertNotIn(vapid.signer().private_key.private_numbers().private_value.to_bytes(32, "big").hex(),
                         VapidKeys.objects.get().private_key)  # encrypted

    def test_an_existing_deployments_key_is_kept(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        old = Vapid(private_key=ec.generate_private_key(ec.SECP256R1()))
        pem = old.private_pem().decode()
        public, _private = vapid._pair(old)
        with override_settings(VAPID_PRIVATE_KEY=pem):
            self.assertEqual(vapid.public_key(), public)

    def test_an_unreadable_key_is_replaced(self):
        key = vapid.public_key()
        VapidKeys.objects.update(private_key="garbage")  # SECRET_KEY changed
        self.assertNotEqual(vapid._row().public_key, key)
        self.assertNotEqual(vapid.public_key(), key)


class SendTests(TestCase):
    def setUp(self):
        self.sub = PushSubscription.objects.create(app="store", language="uk", **subscriptions.parse(browser_subscription()))

    def test_each_subscription_gets_it_in_its_language(self):
        from django.utils.translation import gettext
        with mock.patch("push.send.webpush") as webpush:
            self.assertEqual(send.send_now([self.sub], lambda: {"title": gettext("Order"), "url": "/store/"}), 1)
        message = json.loads(webpush.call_args.args[1])
        self.assertEqual(message["url"], "/store/")
        self.assertNotEqual(message["title"], "Order")  # in Ukrainian
        self.sub.refresh_from_db()
        self.assertIsNotNone(self.sub.last_success_at)

    def test_gone_subscriptions_are_deleted_straight_away(self):
        with mock.patch("push.send.webpush", side_effect=refused(410)):
            send.send_now([self.sub], lambda: {"title": "Hi"})
        self.assertFalse(PushSubscription.objects.exists())

    def test_refused_ones_go_after_three_in_a_row_and_busy_services_are_tried_again(self):
        with mock.patch("push.send.webpush", side_effect=refused(503)):
            send.send_now([self.sub], lambda: {"title": "Hi"})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.failures, 0)
        for _ in range(2):
            with mock.patch("push.send.webpush", side_effect=refused(403)):
                send.send_now(PushSubscription.objects.all(), lambda: {"title": "Hi"})
        self.assertEqual(PushSubscription.objects.get().failures, 2)
        with mock.patch("push.send.webpush"):
            send.send_now(PushSubscription.objects.all(), lambda: {"title": "Hi"})
        self.assertEqual(PushSubscription.objects.get().failures, 0)  # it worked: start counting again
        for _ in range(3):
            with mock.patch("push.send.webpush", side_effect=refused(403)):
                send.send_now(PushSubscription.objects.all(), lambda: {"title": "Hi"})
        self.assertFalse(PushSubscription.objects.exists())

    def test_notify_waits_for_the_commit_and_sends_in_the_background(self):
        with mock.patch("push.send.webpush") as webpush, mock.patch("push.send.background", lambda job: job()):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                send.notify(PushSubscription.objects.all(), lambda: {"title": "Hi"})
            webpush.assert_not_called()
            callbacks[0]()
        webpush.assert_called_once()


class SubscriptionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.ana = User.objects.create_user("ana", password="x")

    def request(self, user=None):
        from django.contrib.auth.models import AnonymousUser
        request = self.factory.post("/", HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) AppleWebKit Chrome/130 Mobile Safari/537.36")
        request.user = user or AnonymousUser()
        return request

    def test_a_guest_subscription_gets_its_account_when_they_sign_in(self):
        guest = subscriptions.save(self.request(), "store", browser_subscription())
        self.assertEqual((guest.user, guest.device, guest.order_updates, guest.event_updates), (None, "Chrome on Android", True, False))
        mine = subscriptions.save(self.request(self.ana), "store", browser_subscription())
        self.assertEqual((mine.pk, mine.user, mine.event_updates, mine.news_updates), (guest.pk, self.ana, True, True))  # what accounts get
        mine.news_updates = False
        mine.save()
        again = subscriptions.save(self.request(self.ana), "store", browser_subscription())
        self.assertFalse(again.news_updates)  # once it's theirs, their choices stay
        fresh = subscriptions.save(self.request(self.ana), "store", browser_subscription(2))
        self.assertTrue(fresh.order_updates and fresh.event_updates and fresh.news_updates)  # an account's new device: everything
        self.assertEqual(PushSubscription.objects.count(), 2)  # the guest's phone (now Ana's) and her new device

    def test_only_its_owner_or_a_guest_can_forget_one(self):
        subscriptions.save(self.request(self.ana), "store", browser_subscription())
        endpoint = browser_subscription()["endpoint"]
        self.assertEqual(subscriptions.forget(endpoint, self.request().user), 0)
        self.assertEqual(subscriptions.forget(endpoint, self.ana), 1)

    def test_bad_subscriptions_are_refused(self):
        for bad in (None, {}, {"endpoint": "http://plain.example/x", "keys": {"p256dh": "a", "auth": "b"}},
                    {"endpoint": "https://x.example/" + "a" * 1100, "keys": {"p256dh": "a", "auth": "b"}}):
            with self.assertRaises(subscriptions.BadSubscription):
                subscriptions.parse(bad)

    def test_device_names(self):
        self.assertEqual(subscriptions.device_name("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit Version/17.4 Mobile/15E148 Safari/604.1"), "Safari on iPhone")
        self.assertEqual(subscriptions.device_name("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36 Edg/130"), "Edge on Windows")
        self.assertEqual(subscriptions.device_name(""), "")


class CourierPushTests(TestCase):
    def setUp(self):
        from users.testing import crew
        self.courier = crew(User.objects.create_user("cara", password="x", is_staff=True), "app.courier")
        self.client.force_login(self.courier)

    def test_the_courier_app_is_installable_and_saves_its_subscription(self):
        from django.contrib.staticfiles import finders
        manifest = self.client.get(reverse("delivery_manifest")).json()
        self.assertEqual((manifest["id"], manifest["scope"], manifest["start_url"]), ("/deliveries/", "/deliveries/", "/deliveries/"))
        self.assertTrue(manifest["description"])  # with screenshots, what Chrome's richer install sheet needs
        self.assertEqual({s["form_factor"] for s in manifest["screenshots"]}, {"narrow", "wide"})
        self.assertIn("maskable", {i["purpose"] for i in manifest["icons"]})
        for picture in manifest["icons"] + manifest["screenshots"]:  # every file is there
            self.assertTrue(finders.find(picture["src"].removeprefix("/static/")), picture["src"])
        worker = self.client.get(reverse("delivery_worker"))
        self.assertEqual(worker["Content-Type"], "text/javascript; charset=utf-8")
        self.assertContains(worker, vapid.public_key())
        self.assertContains(worker, "offline")
        response = self.client.post(reverse("deliveries_save_subscription"), json.dumps(browser_subscription()), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushSubscription.objects.get().user, self.courier)

    def test_couriers_on_shift_get_new_orders(self):
        from deliveries.models import CourierShift
        from deliveries.notifications import trigger_push_notifications
        PushSubscription.objects.create(user=self.courier, app="deliveries", **subscriptions.parse(browser_subscription()))
        PushSubscription.objects.create(user=self.courier, app="store", **subscriptions.parse(browser_subscription(2)))
        CourierShift.objects.create(user=self.courier)
        with mock.patch("push.send.webpush") as webpush, mock.patch("push.send.background", lambda job: job()):
            trigger_push_notifications("New delivery", "Order #4", "See order", "/deliveries/order/4")
        self.assertEqual(webpush.call_count, 1)  # the courier app's subscription, not the store's
        self.assertEqual(json.loads(webpush.call_args.args[1])["url"], "/deliveries/order/4")

    def test_a_renewed_subscription_takes_over_the_old_one(self):
        old = PushSubscription.objects.create(user=self.courier, app="deliveries", **subscriptions.parse(browser_subscription()))
        body = {"subscription": browser_subscription(2), "replaces": old.endpoint}
        self.client.logout()  # the service worker has no session to go on
        self.assertTrue(self.client.post(reverse("delivery_push_renew"), json.dumps(body), content_type="application/json").json()["saved"])
        old.refresh_from_db()
        self.assertEqual((old.endpoint, old.user), (browser_subscription(2)["endpoint"], self.courier))
        unknown = {"subscription": browser_subscription(3), "replaces": "https://fcm.googleapis.com/fcm/send/nobody"}
        self.assertFalse(self.client.post(reverse("delivery_push_renew"), json.dumps(unknown), content_type="application/json").json()["saved"])


class EventUpdateTests(TestCase):
    """Event updates (events/announce.py: notify_later) to the store devices that turned them on."""

    def test_a_new_event_goes_to_devices_with_event_updates_on(self):
        import datetime
        from django.utils import timezone
        from events.announce import notify_later
        from events.models import Event
        from push.models import NotificationSwitch
        on = PushSubscription.objects.create(app="store", event_updates=True, **subscriptions.parse(browser_subscription()))
        PushSubscription.objects.create(app="store", event_updates=False, **subscriptions.parse(browser_subscription(2)))  # order updates only
        day = timezone.localdate() + datetime.timedelta(days=3)
        trivia = Event.objects.create(kind=Event.OPEN, title="Trivia night", start_date=day, end_date=day, opens=19 * 60, closes=22 * 60)
        past = Event.objects.create(kind=Event.CLOSED, title="Long ago", start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2020, 1, 1))
        with mock.patch("push.send.webpush") as webpush, mock.patch("push.send.background", lambda job: job()), \
                self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(notify_later([trivia, past]), 1)
        self.assertEqual(webpush.call_count, 1)
        self.assertEqual(webpush.call_args.args[0]["endpoint"], on.endpoint)
        message = json.loads(webpush.call_args.args[1])
        self.assertEqual(message["title"], "Trivia night")
        self.assertIn("7 PM – 10 PM", message["body"])
        NotificationSwitch.objects.create(kind="event_updates", enabled=False)
        self.assertEqual(notify_later([trivia]), 0)


class NotificationsPageTests(TestCase):
    """The admin's Notifications page (admin_portal/notifications.py)."""

    def setUp(self):
        from users.testing import crew
        self.owner = User.objects.create_superuser("owner", password="x")
        self.viewer = crew(User.objects.create_user("vi", password="x", is_staff=True), "app.admin", "admin.notifications.view")
        self.client.force_login(self.owner)
        self.url = reverse("admin_notifications")

    def test_switches_are_saved_with_save(self):
        from push import kinds
        page = self.client.get(self.url)
        self.assertContains(page, "Order updates")
        self.assertContains(page, "Event updates")
        self.client.post(self.url, {"op": "flip:event_updates"})
        self.assertTrue(kinds.enabled("event_updates"))  # a draft until Save
        self.client.post(self.url, {"op": "save"})
        self.assertFalse(kinds.enabled("event_updates"))
        self.assertTrue(kinds.enabled("order_updates"))

    def test_a_test_notification_goes_to_your_own_devices(self):
        page = self.client.post(self.url, {"op": "test"}, follow=True)
        self.assertContains(page, "None of your devices")
        PushSubscription.objects.create(app="store", user=self.owner, **subscriptions.parse(browser_subscription()))
        with mock.patch("push.send.webpush") as webpush:
            self.assertContains(self.client.post(self.url, {"op": "test"}, follow=True), "Sent to 1 of your devices.")
        self.assertEqual(json.loads(webpush.call_args.args[1])["title"], "Test notification")

    def test_viewing_isnt_editing(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.post(self.url, {"op": "flip:order_updates"})
        self.client.post(self.url, {"op": "save"})  # refused: back with a message
        from push import kinds
        self.assertTrue(kinds.enabled("order_updates"))


class AccountNotificationsTests(TestCase):
    """The account's Notifications page (online_store/account.py: notifications)."""

    def setUp(self):
        self.ana = User.objects.create_user("ana", password="x")
        self.bo = User.objects.create_user("bo", password="x")
        self.phone = PushSubscription.objects.create(app="store", user=self.ana, device="Chrome on Android", **subscriptions.parse(browser_subscription()))
        self.bos = PushSubscription.objects.create(app="store", user=self.bo, **subscriptions.parse(browser_subscription(2)))
        self.client.force_login(self.ana)
        self.url = reverse("account_notifications")

    def test_devices_what_they_get_and_removing_one(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Chrome on Android")
        self.assertNotContains(page, self.bos.endpoint[:30])
        self.assertTrue(self.phone.event_updates and self.phone.news_updates)  # on until they say otherwise
        self.client.post(self.url, {"op": f"toggle:{self.phone.id}:events"})
        self.phone.refresh_from_db()
        self.assertFalse(self.phone.event_updates)
        self.client.post(self.url, {"op": f"toggle:{self.phone.id}:news"})  # listmonk's push campaigns
        self.phone.refresh_from_db()
        self.assertFalse(self.phone.news_updates)
        self.client.post(self.url, {"op": f"toggle:{self.bos.id}:events"})  # someone else's: nothing
        self.bos.refresh_from_db()
        self.assertTrue(self.bos.event_updates)
        self.client.post(self.url, {"op": f"remove:{self.bos.id}"})
        self.assertTrue(PushSubscription.objects.filter(pk=self.bos.pk).exists())
        self.client.post(self.url, {"op": f"remove:{self.phone.id}"})
        self.assertFalse(PushSubscription.objects.filter(pk=self.phone.pk).exists())

    def test_signed_out_people_sign_in_first(self):
        self.client.logout()
        self.assertTrue(self.client.get(self.url).url.startswith(reverse("store_signin")))


@override_settings(LISTMONK_URL="https://mail.example.com", LISTMONK_API_USER="pavilion", LISTMONK_API_TOKEN="secret")
class ListmonkMessengerTests(TestCase):
    """Push campaigns from listmonk (push/listmonk.py): Pavilion as a listmonk messenger."""

    def setUp(self):
        from misc_tools.tests import FakeListmonk
        cache.clear()
        self.fake = FakeListmonk()
        patcher = mock.patch("misc_tools.listmonk.api", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ana = User.objects.create_user("ana", email="ana@example.com", password="x")
        self.url = reverse("listmonk_messenger")

    def phone(self, n=1, user=None, news=True, app="store"):
        return PushSubscription.objects.create(app=app, user=user or self.ana, news_updates=news, **subscriptions.parse(browser_subscription(n)))

    def post(self, body, auth=True):
        from push import listmonk as messenger
        headers = {}
        if auth:
            token = base64.b64encode(f"pavilion:{messenger.password()}".encode()).decode()
            headers["HTTP_AUTHORIZATION"] = f"Basic {token}"
        return self.client.post(self.url, json.dumps(body), content_type="application/json", **headers)

    def payload(self, attribs, body="Two-for-one tacos tonight, 5 to 7. https://shop.example.com/store/menu@TrackLink", content_type="plain"):
        return {"subject": "Taco Tuesday", "body": body, "content_type": content_type, "from_email": "",
                "recipients": [{"uuid": "u-1", "email": "ana@example.com", "name": "Ana", "attribs": attribs, "status": "enabled"}],
                "campaign": {"uuid": "c-1", "name": "Tacos", "tags": []}}

    def test_a_campaign_message_goes_to_that_accounts_devices_with_news_on(self):
        mine = self.phone(1)
        quiet = self.phone(2, news=False)
        bo = User.objects.create_user("bo", password="x")
        theirs = self.phone(3, user=bo)
        attribs = {"pavilion_id": self.ana.pk, "pavilion_push": [mine.pk, quiet.pk, theirs.pk]}  # someone else's id can't sneak in
        with mock.patch("push.send.webpush") as webpush, mock.patch("push.send.pooled", lambda job: job()):
            response = self.post(self.payload(attribs))
        self.assertEqual((response.status_code, response.json()["devices"]), (200, 1))
        self.assertEqual(webpush.call_args.args[0]["endpoint"], mine.endpoint)
        message = json.loads(webpush.call_args.args[1])
        self.assertEqual((message["title"], message["body"], message["url"], message["tag"]),
                         ("Taco Tuesday", "Two-for-one tacos tonight, 5 to 7.", "https://shop.example.com/store/menu@TrackLink", "campaign-c-1"))

    def test_another_apps_attribute_reaches_nothing(self):
        mine = self.phone(1)
        with mock.patch("push.send.webpush") as webpush, mock.patch("push.send.pooled", lambda job: job()):
            self.assertEqual(self.post(self.payload({"pavilion_id": self.ana.pk, "push": [mine.pk]})).json()["devices"], 0)
        webpush.assert_not_called()

    def test_html_becomes_text_and_nobody_listed_is_still_fine(self):
        from push import listmonk as messenger
        built = messenger.message("Hi", "<p>New <b>pie</b> &amp; coffee</p><p>Come by!</p>", "html", {"uuid": "x"})
        self.assertEqual((built["body"], built["url"]), ("New pie & coffee Come by!", reverse("store_home")))
        with mock.patch("push.send.webpush") as webpush, mock.patch("push.send.pooled", lambda job: job()):
            self.assertEqual(self.post(self.payload({"email_only": True})).status_code, 200)  # 200, or listmonk retries
        webpush.assert_not_called()

    def test_only_listmonk_signs_in(self):
        self.assertEqual(self.post(self.payload({}), auth=False).status_code, 401)
        wrong = base64.b64encode(b"pavilion:nope").decode()
        self.assertEqual(self.client.post(self.url, "{}", content_type="application/json", HTTP_AUTHORIZATION=f"Basic {wrong}").status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_turned_off_on_the_notifications_page(self):
        from push.models import NotificationSwitch
        NotificationSwitch.objects.create(kind="campaigns", enabled=False)
        mine = self.phone(1)
        with mock.patch("push.send.webpush") as webpush:
            self.assertTrue(self.post(self.payload({"pavilion_id": self.ana.pk, "pavilion_push": [mine.pk]})).json()["off"])
        webpush.assert_not_called()

    def test_setting_it_up_in_listmonk(self):
        from admin_portal.models import StoreSettings
        from push import listmonk as messenger
        messenger.install("https://shop.example.com/push/listmonk")
        (entry,) = self.fake.settings["messengers"]
        self.assertEqual((entry["name"], entry["root_url"], entry["username"], entry["password"], entry["enabled"]),
                         ("pavilion-push", "https://shop.example.com/push/listmonk", "pavilion", messenger.password(), True))
        self.assertEqual(self.fake.settings["smtp"][0]["password"], "hunter2")  # its other passwords stay as they were
        template = self.fake.templates[StoreSettings.load().push_messenger_template]
        self.assertEqual((template["type"], template["body"]), ("campaign", '{{ template "content" . }}'))
        messenger.install("https://shop.example.com/push/listmonk")  # again: updated, not added twice
        self.assertEqual((len(self.fake.settings["messengers"]), len(self.fake.templates)), (1, 1))
        self.assertTrue(messenger.status(force=True)["messenger"])

    def test_devices_with_news_are_a_subscriber_attribute(self):
        from misc_tools import listmonk
        with mock.patch("misc_tools.listmonk._background", lambda job: job()), self.captureOnCommitCallbacks(execute=True):
            mine = self.phone(1)
        self.assertEqual(self.fake.by_email("ana@example.com")["attribs"]["pavilion_push"], [mine.pk])
        with mock.patch("misc_tools.listmonk.sync_later") as sync:
            self.phone(2, news=False)  # nothing for listmonk to know
            sync.assert_not_called()
            mine.news_updates = False
            mine.save()
            sync.assert_called_once()
        listmonk.sync_user(self.ana)
        self.assertEqual(self.fake.by_email("ana@example.com")["attribs"]["pavilion_push"], [])

    def test_the_notifications_page_sets_it_up(self):
        owner = User.objects.create_superuser("owner", password="x")
        self.client.force_login(owner)
        page = self.client.get(reverse("admin_notifications"))
        self.assertContains(page, "Not set up in listmonk yet")
        self.assertEqual(page.context["x"]["st"]["url"], "http://testserver/push/listmonk")
        self.client.post(reverse("admin_notifications"), {"op": "messenger", "messenger_url": "https://shop.example.com/push/listmonk"})
        self.assertEqual(self.fake.settings["messengers"][0]["root_url"], "https://shop.example.com/push/listmonk")
        self.assertContains(self.client.get(reverse("admin_notifications")), "Set up in listmonk as")
