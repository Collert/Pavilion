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
        guest = subscriptions.save(self.request(), "store", browser_subscription(), topics=["orders"])
        self.assertEqual((guest.user, guest.device, guest.topics), (None, "Chrome on Android", ["orders"]))
        mine = subscriptions.save(self.request(self.ana), "store", browser_subscription(), topics=["orders", "events"])
        self.assertEqual((mine.pk, mine.user, mine.topics), (guest.pk, self.ana, ["orders"]))  # its topics stay
        self.assertEqual(PushSubscription.objects.count(), 1)

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
        manifest = self.client.get(reverse("delivery_manifest")).json()
        self.assertEqual((manifest["scope"], manifest["start_url"]), ("/deliveries/", "/deliveries/"))
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
