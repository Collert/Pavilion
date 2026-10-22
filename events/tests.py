import datetime

from django.test import TestCase
from django.utils import timezone

from .hours import DELIVERY, PICKUP, Hours, cut
from .models import Event, StoreHours


class HoursTests(TestCase):
    """events/hours.py: the week, closures, cutoffs, hours past midnight and pausing."""

    def setUp(self):
        week = [{"open": True, "r": [[480, 780], [840, 1260]]}] * 5 + [{"open": True, "r": [[540, 1500]]}, {"open": False, "r": [[540, 900]]}]
        self.cfg = StoreHours.objects.create(pk=1, week=week, pickup_after=15, pickup_before=20, delivery_after=30, delivery_before=45)
        self.monday = timezone.localdate() - datetime.timedelta(days=timezone.localdate().weekday()) + datetime.timedelta(days=7)

    def at(self, day, hour, minute=0):
        return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))

    def test_cutoffs_with_and_without_breaks(self):
        self.assertEqual(cut([(480, 780), (840, 1260)], 15, 20), [(495, 760), (855, 1240)])
        self.assertEqual(cut([(480, 780), (840, 1260)], 15, 20, around_breaks=False), [(495, 780), (840, 1240)])
        self.assertEqual(cut([(480, 500)], 15, 20), [])  # too short to take orders at all

    def test_ranges_spans_and_closures(self):
        hours = Hours()
        self.assertEqual(hours.ranges(self.monday), [(480, 780), (840, 1260)])
        self.assertEqual(hours.ranges(self.monday, PICKUP), [(495, 760), (855, 1240)])
        self.assertEqual(hours.ranges(self.monday, DELIVERY), [(510, 735), (870, 1215)])
        sunday = self.monday + datetime.timedelta(days=6)
        self.assertEqual(hours.ranges(sunday), [])  # switched off, its hours kept for later
        # Saturday runs till 1 AM: Sunday's calendar day starts with it
        self.assertEqual(hours.on_day(sunday), [(self.at(sunday, 0), self.at(sunday, 1))])
        self.assertEqual(hours.current(self.at(sunday, 0, 30)), (self.at(sunday - datetime.timedelta(days=1), 9), self.at(sunday, 1)))
        everything = {"kind": "closed", "dine_in": True, "pickup": True, "delivery": True}
        Event.objects.create(title="Short day", start_date=self.monday, end_date=self.monday, opens=900, closes=1680, **everything)
        tuesday = self.monday + datetime.timedelta(days=1)
        Event.objects.create(title="Holiday", start_date=tuesday, end_date=tuesday, all_day=True, **everything)
        hours = Hours()
        self.assertEqual(hours.ranges(self.monday), [(480, 780), (840, 900)])
        self.assertEqual(hours.ranges(self.monday + datetime.timedelta(days=1)), [])
        self.assertEqual(hours.next_start(self.at(self.monday, 16)), self.at(self.monday + datetime.timedelta(days=2), 8))

    def test_pause(self):
        now = timezone.now()
        self.assertIsNone(Hours().paused_until(now))
        self.cfg.paused_until = now + datetime.timedelta(minutes=15)
        self.cfg.save()
        self.assertEqual(Hours().paused_until(now), self.cfg.paused_until)
        self.assertIsNone(Hours().paused_until(now + datetime.timedelta(minutes=16)))


class EventLayerTests(TestCase):
    """Events over Store hours: openings add hours for the channels they take, closures win where they overlap."""

    def setUp(self):
        week = [{"open": True, "r": [[480, 1260]]}] * 6 + [{"open": False, "r": []}]
        StoreHours.objects.create(pk=1, week=week, pickup_after=0, pickup_before=0, delivery_after=0, delivery_before=0)
        self.monday = timezone.localdate() - datetime.timedelta(days=timezone.localdate().weekday()) + datetime.timedelta(days=7)
        self.sunday = self.monday + datetime.timedelta(days=6)

    def event(self, **kw):
        from .models import Event
        day = kw.pop("day", self.monday)
        kw.setdefault("title", "E")
        return Event.objects.create(start_date=day, end_date=kw.pop("end", day), **kw)

    def test_open_event_adds_hours_for_its_channels(self):
        self.event(day=self.sunday, kind="open", opens=600, closes=900, dine_in=True, pickup=False, delivery=False)
        hours = Hours()
        self.assertEqual(hours.ranges(self.sunday), [(600, 900)])
        self.assertEqual(hours.ranges(self.sunday, PICKUP), [])  # dine-in only
        self.event(kind="open", opens=1200, closes=1380, dine_in=True, pickup=True)
        self.assertEqual(Hours().ranges(self.monday, PICKUP), [(480, 1380)])  # extends the usual hours

    def test_closures_by_channel_and_all_day(self):
        self.event(kind="closed", opens=780, closes=840, dine_in=True, pickup=True, delivery=True)
        self.event(kind="closed", dine_in=False, pickup=False, delivery=True, opens=1080, closes=1440, end=self.monday + datetime.timedelta(days=1))
        hours = Hours()
        self.assertEqual(hours.ranges(self.monday), [(480, 780), (840, 1260)])
        self.assertEqual(hours.ranges(self.monday, DELIVERY), [(480, 780), (840, 1080)])
        tuesday = self.monday + datetime.timedelta(days=1)
        self.assertEqual(hours.ranges(tuesday, PICKUP), [(480, 1260)])
        self.event(day=tuesday, kind="closed", all_day=True, dine_in=False, pickup=True, delivery=True)
        hours = Hours()
        self.assertEqual((hours.ranges(tuesday), hours.ranges(tuesday, PICKUP)), ([(480, 1260)], []))  # dine-in stays open

    def test_store_banner(self):
        from online_store.storefront import event_banner
        today = timezone.localdate()
        self.assertIsNone(event_banner(today))
        self.event(day=today + datetime.timedelta(days=5), kind="closed", all_day=True, banner_days=3, description="Training")
        self.assertIsNone(event_banner(today))  # not yet
        self.event(day=today + datetime.timedelta(days=1), kind="open", title="Trivia night", opens=1140, closes=1320, banner_days=3)
        banner = event_banner(today)
        self.assertEqual((banner["kind"], banner["head"]), ("open", "Trivia night · tomorrow, 7 PM–10 PM."))
