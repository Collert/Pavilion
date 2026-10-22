import datetime

from django.test import TestCase
from django.utils import timezone

from .hours import DELIVERY, PICKUP, Hours, cut
from .models import SpecialHours, StoreHours


class HoursTests(TestCase):
    """events/hours.py: the week, special dates, cutoffs, hours past midnight and pausing."""

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

    def test_ranges_spans_and_specials(self):
        hours = Hours()
        self.assertEqual(hours.ranges(self.monday), [(480, 780), (840, 1260)])
        self.assertEqual(hours.ranges(self.monday, PICKUP), [(495, 760), (855, 1240)])
        self.assertEqual(hours.ranges(self.monday, DELIVERY), [(510, 735), (870, 1215)])
        sunday = self.monday + datetime.timedelta(days=6)
        self.assertEqual(hours.ranges(sunday), [])  # switched off, its hours kept for later
        # Saturday runs till 1 AM: Sunday's calendar day starts with it
        self.assertEqual(hours.on_day(sunday), [(self.at(sunday, 0), self.at(sunday, 1))])
        self.assertEqual(hours.current(self.at(sunday, 0, 30)), (self.at(sunday - datetime.timedelta(days=1), 9), self.at(sunday, 1)))
        SpecialHours.objects.create(date=self.monday, closed=False, opens=600, closes=900, note="Short day")
        SpecialHours.objects.create(date=self.monday + datetime.timedelta(days=1), closed=True, note="Closed for a holiday")
        hours = Hours()
        self.assertEqual(hours.ranges(self.monday), [(600, 900)])
        self.assertEqual(hours.ranges(self.monday + datetime.timedelta(days=1)), [])
        self.assertEqual(hours.next_start(self.at(self.monday, 16)), self.at(self.monday + datetime.timedelta(days=2), 8))

    def test_pause(self):
        now = timezone.now()
        self.assertIsNone(Hours().paused_until(now))
        self.cfg.paused_until = now + datetime.timedelta(minutes=15)
        self.cfg.save()
        self.assertEqual(Hours().paused_until(now), self.cfg.paused_until)
        self.assertIsNone(Hours().paused_until(now + datetime.timedelta(minutes=16)))
