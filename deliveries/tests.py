"""The courier app: shifts, taking a delivery, the steps to the door, and how delivery orders reach the kitchen."""
import io
import json
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from online_store.models import RejectedOrder
from pos_server.models import Order, OrderStation, active_orders_queryset
from pos_server.orders import create_order
from pos_server.tests import MenuFixture
from .models import CourierShift, Delivery, hidden_order_ids
from . import routes
from users.testing import crew

MEDIA = tempfile.mkdtemp()


def jpeg(size=(2400, 1800), exif=None):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, "#F4A93B").save(out, "JPEG", **({"exif": exif} if exif else {}))
    return SimpleUploadedFile("door.jpg", out.getvalue(), content_type="image/jpeg")


@override_settings(ENFORCE_DEVICE_CHECK=False, MEDIA_ROOT=MEDIA, GOOGLE_API_KEY=None)
class CourierTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.sam = crew(User.objects.create_user("sam", first_name="Sam", password="x", is_staff=True))
        cls.kai = crew(User.objects.create_user("kai", first_name="Kai", password="x", is_staff=True))
        cls.cook = crew(User.objects.create_user("cook", password="x", is_staff=True))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()  # travel-time lookups that failed are remembered by delivery id
        self.client.force_login(self.sam)
        self.notify = mock.patch("deliveries.signals.trigger_push_notifications").start()
        self.addCleanup(mock.patch.stopall)

    def delivery_order(self, name="Maya T."):
        order = create_order(lines=[self.dish(self.burger, qty=2), self.dish(self.tea)], channel="delivery",
                             initial_status=OrderStation.STATUS_PENDING, name=name, phone=6045550147, to_go_order=True)
        return Delivery.objects.create(order=order, destination="2145 W 4th Ave, Vancouver, BC V6K 1N7", address_2="Unit 204",
                                       phone=6045550147, instructions="Side door, buzz 204")

    def start(self, user=None, mode="bicycling"):
        if user:
            self.client.force_login(user)
        return self.client.post(reverse("delivery_shift"), {"action": "start", "mode": mode})

    def kds(self, order, action, **extra):
        self.client.force_login(self.cook)
        response = self.client.post(reverse("order_marking"), json.dumps(
            {"orderId": order.id, "action": action, "filters": ["kitchen", "bar"], **extra}), content_type="application/json")
        self.client.force_login(self.sam)
        return response

    def kds_ids(self):
        return set(active_orders_queryset().values_list("id", flat=True))

    # ---------- shifts ----------

    def test_start_screen_until_on_shift(self):
        response = self.client.get(reverse("delivery_orders"))
        self.assertTemplateUsed(response, "deliveries/start.html")
        self.assertContains(response, "Hey Sam")
        self.start(mode="teleport")
        self.assertFalse(CourierShift.objects.exists())
        self.start()
        self.assertEqual(CourierShift.objects.get().mode, "bicycling")
        self.assertTemplateUsed(self.client.get(reverse("delivery_orders")), "deliveries/list.html")

    def test_clock_is_outside_the_live_regions(self):
        """staff-page.js ticks the clock elements it found on load; a region swap would replace them with empty ones."""
        from html.parser import HTMLParser
        VOID = {"input", "img", "meta", "link", "br", "hr", "source"}

        class Finder(HTMLParser):
            stack, clock_in_region = [], None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get("id") == "rail-time":
                    self.clock_in_region = any(self.stack)
                if tag not in VOID:
                    self.stack.append("data-region" in attrs)

            def handle_endtag(self, tag):
                if tag not in VOID and self.stack:
                    self.stack.pop()

        self.start()
        for url in (reverse("delivery_orders"), reverse("delivery_orders") + "?end=1"):
            finder = Finder()
            finder.feed(self.client.get(url).content.decode())
            self.assertIs(finder.clock_in_region, False, url)

    def test_customers_cannot_use_the_courier_app(self):
        customer = User.objects.create_user("customer", password="x")
        self.client.force_login(customer)
        self.assertEqual(self.client.get(reverse("delivery_orders")).status_code, 302)
        self.assertEqual(self.client.post(reverse("delivery_shift"), {"action": "start", "mode": "driving"}).status_code, 302)
        self.assertFalse(CourierShift.objects.exists())

    def test_end_shift_waits_for_the_delivery(self):
        delivery = self.delivery_order()
        self.start()
        self.client.post(reverse("delivery_order", args=[delivery.id]), {"action": "accept"})
        self.assertContains(self.client.get(reverse("delivery_orders") + "?end=1"), "Finish delivery #")
        self.client.post(reverse("delivery_shift"), {"action": "end"})
        self.assertTrue(CourierShift.objects.exists())
        Delivery.objects.filter(pk=delivery.pk).update(delivered_at=delivery.timestamp)
        self.client.post(reverse("delivery_shift"), {"action": "end"})
        self.assertFalse(CourierShift.objects.exists())

    # ---------- the kitchen sees delivery orders once a courier takes them ----------

    def test_kitchen_waits_for_a_courier(self):
        delivery = self.delivery_order()
        self.assertIn(delivery.order_id, self.kds_ids())  # nobody on shift: staff must see it to reject it
        self.start()
        self.assertNotIn(delivery.order_id, self.kds_ids())
        self.assertContains(self.client.get(reverse("delivery_orders")), "#%s" % delivery.order_id)
        self.client.post(reverse("delivery_order", args=[delivery.id]), {"action": "accept"})
        self.assertIn(delivery.order_id, self.kds_ids())
        self.assertEqual(Delivery.objects.get().courier, self.sam)

    def test_order_shows_on_the_kitchen_when_every_courier_passed(self):
        delivery = self.delivery_order()
        self.start()
        self.start(self.kai, "driving")
        self.client.post(reverse("delivery_order", args=[delivery.id]), {"action": "pass"})
        self.assertNotIn(delivery.order_id, self.kds_ids())
        self.assertNotContains(self.client.get(reverse("delivery_orders")), reverse("delivery_order", args=[delivery.id]))
        self.client.force_login(self.sam)
        self.client.post(reverse("delivery_order", args=[delivery.id]), {"action": "pass"})
        self.assertEqual(hidden_order_ids(), [])
        self.client.force_login(self.cook)
        feed = self.client.get(reverse("order_marking")).context["orders"]
        self.assertEqual([(o["order_id"], o["courier"]) for o in feed], [(delivery.order_id, None)])

    def test_two_couriers_cannot_take_the_same_order(self):
        delivery = self.delivery_order()
        self.start()
        self.start(self.kai)
        self.client.post(reverse("delivery_order", args=[delivery.id]), {"action": "accept"})
        self.client.force_login(self.sam)
        response = self.client.post(reverse("delivery_order", args=[delivery.id]), {"action": "accept"}, follow=True)
        self.assertContains(response, "Kai already took order")
        self.assertEqual(Delivery.objects.get().courier, self.kai)

    def test_one_delivery_at_a_time(self):
        first, second = self.delivery_order(), self.delivery_order("Leo K.")
        self.start()
        self.client.post(reverse("delivery_order", args=[first.id]), {"action": "accept"})
        self.assertContains(self.client.get(reverse("delivery_order", args=[second.id])), "Finish #%s first" % first.order_number)
        self.client.post(reverse("delivery_order", args=[second.id]), {"action": "accept"})
        self.assertIsNone(Delivery.objects.get(pk=second.pk).courier)

    # ---------- from the shelf to the door ----------

    def test_whole_delivery(self):
        delivery = self.delivery_order()
        self.start()
        current = reverse("delivery_current")
        self.client.post(reverse("delivery_order", args=[delivery.id]), {"action": "accept"})
        self.assertContains(self.client.get(current), "Waiting for the restaurant to accept")
        self.client.post(current, {"action": "pickup"})  # too early
        self.assertIsNone(Delivery.objects.get().picked_up_at)

        self.kds(delivery.order, "confirm")
        response = self.client.get(current)
        self.assertContains(response, "Kitchen is preparing it")
        self.assertContains(response, "data-bar-ends")
        with self.captureOnCommitCallbacks(execute=True):
            self.kds(delivery.order, "ready")
        self.assertContains(self.client.get(current), "Your order is on the shelf")
        self.assertTrue(any(call.kwargs.get("user_ids") == [self.sam.id] for call in self.notify.call_args_list))

        self.client.post(current, {"action": "pickup"})
        self.assertTrue(Order.objects.get().picked_up)  # off the kitchen display
        self.assertNotIn(delivery.order_id, self.kds_ids())
        self.assertContains(self.client.get(current), "Heading to Maya")
        self.client.post(current, {"action": "arrive"})
        self.assertContains(self.client.get(current), "Confirm the drop-off")

        # Left at the door: a photo is required
        self.client.post(current, {"action": "complete", "handoff": "door"})
        self.assertIsNone(Delivery.objects.get().delivered_at)
        response = self.client.post(current, {"action": "complete", "handoff": "door", "photo": jpeg()})
        delivery.refresh_from_db()
        self.assertRedirects(response, reverse("delivery_done", args=[delivery.id]))
        self.assertEqual(Order.objects.get().progress_status(), 6)
        self.assertEqual((delivery.phase(), delivery.handoff), ("done", "door"))
        self.assertContains(self.client.get(reverse("delivery_done", args=[delivery.id])), "Delivered!")
        self.assertRedirects(self.client.get(current), reverse("delivery_orders"))

    def test_proof_photo_is_shrunk_and_stripped(self):
        from PIL import Image
        delivery = self.delivery_order()
        self.start()
        Delivery.objects.filter(pk=delivery.pk).update(courier=self.sam, picked_up_at=delivery.timestamp, arrived_at=delivery.timestamp)
        exif = Image.Exif()
        exif[0x010F] = "PhoneMaker"
        self.client.post(reverse("delivery_current"), {"action": "complete", "handoff": "door", "photo": jpeg(exif=exif.tobytes())})
        delivery.refresh_from_db()
        with Image.open(delivery.photo.path) as photo:
            self.assertEqual(max(photo.size), 1600)
            self.assertEqual(len(photo.getexif()), 0)

    def test_handed_over_needs_no_photo(self):
        delivery = self.delivery_order()
        self.start()
        Delivery.objects.filter(pk=delivery.pk).update(courier=self.sam, picked_up_at=delivery.timestamp, arrived_at=delivery.timestamp)
        self.client.post(reverse("delivery_current"), {"action": "complete", "handoff": "hand"})
        self.assertEqual(Delivery.objects.get().handoff, "hand")

    def test_not_a_photo(self):
        delivery = self.delivery_order()
        self.start()
        Delivery.objects.filter(pk=delivery.pk).update(courier=self.sam, picked_up_at=delivery.timestamp, arrived_at=delivery.timestamp)
        bogus = SimpleUploadedFile("door.jpg", b"not an image", content_type="image/jpeg")
        response = self.client.post(reverse("delivery_current"), {"action": "complete", "handoff": "door", "photo": bogus}, follow=True)
        self.assertContains(response, "That file isn&#x27;t a photo.")
        self.assertIsNone(Delivery.objects.get().delivered_at)

    # ---------- the restaurant says no ----------

    def test_restaurant_declines_after_the_courier_accepted(self):
        delivery = self.delivery_order()
        self.start()
        self.client.post(reverse("delivery_order", args=[delivery.id]), {"action": "accept"})
        self.assertEqual(self.kds(delivery.order, "reject", reasons=[], text="Out of buns").status_code, 200)
        self.assertFalse(Order.objects.exists())
        response = self.client.get(reverse("delivery_current"))
        self.assertTemplateUsed(response, "deliveries/declined.html")
        self.assertContains(response, "Out of buns")
        self.client.post(reverse("delivery_current"), {"action": "dismiss"})
        self.assertFalse(Delivery.objects.exists())

    def test_rejected_before_anyone_took_it(self):
        delivery = self.delivery_order()
        RejectedOrder.objects.create(order_id=delivery.order_id, reason="Closed")
        delivery.order.delete()
        self.assertFalse(Delivery.objects.exists())

    # ---------- travel times and push ----------

    @override_settings(GOOGLE_API_KEY="key")
    @mock.patch("deliveries.routes.origin", new=lambda: "1200 Main St")
    def test_travel_times_are_looked_up_once(self):
        delivery = self.delivery_order()
        self.start()
        answer = {"rows": [{"elements": [{"status": "OK", "duration": {"value": 420}, "distance": {"value": 2600}}]}]}
        with mock.patch("deliveries.routes.requests.get") as get:
            get.return_value.json.return_value = answer
            response = self.client.get(reverse("delivery_orders"))
            self.client.get(reverse("delivery_orders"))
        self.assertEqual(get.call_count, len(routes.MODES))
        delivery.refresh_from_db()
        self.assertEqual((delivery.distance_m, delivery.travel_seconds["bicycling"]), (2600, 420))
        self.assertContains(response, "2.6 km")

    @override_settings(GOOGLE_API_KEY="key")
    @mock.patch("deliveries.routes.origin", new=lambda: "1200 Main St")
    def test_a_failed_lookup_is_not_retried_straight_away(self):
        import requests
        self.delivery_order()
        self.start()
        with mock.patch("deliveries.routes.requests.get", side_effect=requests.ConnectionError) as get:
            self.client.get(reverse("delivery_orders"))
            self.client.get(reverse("delivery_orders"))
        self.assertEqual(get.call_count, 1)

    def test_new_orders_are_pushed_to_couriers(self):
        with self.captureOnCommitCallbacks(execute=True):
            delivery = self.delivery_order()
        self.assertEqual(self.notify.call_args.args[3], reverse("delivery_order", args=[delivery.id]))

    def test_push_subscription_needs_staff_and_csrf(self):
        body = json.dumps({"endpoint": "https://push.example/1", "keys": {"p256dh": "a", "auth": "b"}})
        self.assertEqual(self.client.post(reverse("deliveries_save_subscription"), body, content_type="application/json").status_code, 200)
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.sam)
        self.assertEqual(strict.post(reverse("deliveries_save_subscription"), body, content_type="application/json").status_code, 403)
