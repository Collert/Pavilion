import datetime
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from users.models import Role, Staff

from . import schedule
from .models import Availability, ParSet, Shift, TimeOff

MON = datetime.date(2026, 10, 19)
T = lambda h, m=0: h * 60 + m  # noqa: E731


class ScheduleTests(TestCase):
    """team/schedule.py: a day's needs from the pars, what's wrong with a shift, publishing."""

    def setUp(self):
        self.cook = Role.objects.get_or_create(name="Cook")[0]
        self.cashier = Role.objects.get_or_create(name="Cashier")[0]
        self.marco = User.objects.create_user("marco", first_name="Marco", email="marco@example.com", is_staff=True)
        Staff.objects.create(user=self.marco).roles.add(self.cook)

    def test_needs_weekly_then_seasonal_adds_or_replaces(self):
        weekly = schedule.weekly()
        weekly.pars = {"4": {str(self.cook.id): [[T(7), T(21), 1], [T(17), T(19), 2]]}}
        weekly.save()
        friday = MON + datetime.timedelta(days=4)
        need = schedule.needs(friday)
        self.assertEqual((need[self.cook.id][6], need[self.cook.id][7], need[self.cook.id][17], need[self.cook.id][19]), (0, 1, 2, 1))
        ParSet.objects.create(name="Jazz fest", kind=ParSet.SEASON, mode=ParSet.ADD, start=friday, end=friday, days=[4],
                              pars={"4": {str(self.cook.id): [[T(17), T(22), 1]]}})
        self.assertEqual(schedule.needs(friday)[self.cook.id][17:22], [3, 3, 2, 2, 1])
        ParSet.objects.create(name="Quiet week", kind=ParSet.SEASON, mode=ParSet.REPLACE, start=friday, end=friday, days=[4],
                              pars={"4": {str(self.cashier.id): [[T(11), T(15), 1]]}})
        self.assertEqual(set(schedule.needs(friday)), {self.cashier.id})  # replaces the day
        self.assertEqual(schedule.needs(friday + datetime.timedelta(days=7)), {self.cook.id: need[self.cook.id]})  # seasons end

    def test_issues(self):
        day = MON
        TimeOff.objects.create(user=self.marco, first_day=day, last_day=day, all_day=False, start=T(16), end=T(21), category="Appointment",
                               status=TimeOff.APPROVED)
        Availability.objects.create(user=self.marco, status=Availability.APPROVED, effective=day, windows={"0": [[T(6), T(15)]]})
        a = Shift.objects.create(user=self.marco, role=self.cook, date=day, start=T(7), end=T(15))
        b = Shift.objects.create(user=self.marco, role=self.cashier, date=day, start=T(14), end=T(18))
        ctx = {"time_off": schedule.time_off_on([self.marco.id], day), "avail": schedule.availability_on([self.marco.id], day),
               "shifts": [a, b], "roles": {self.marco.id: {self.cook.id}}, "names": {self.marco.id: "Marco"},
               "role_names": {self.cook.id: "Cook", self.cashier.id: "Cashier"}}
        self.assertEqual(schedule.issues(a, ctx), ["Overlaps their cashier shift 2p–6p."])
        self.assertEqual(schedule.issues(b, ctx), ["Marco is off (appointment).", "Marco isn’t available 3p–12a.",
                                                   "Overlaps their cook shift 7a–3p.", "Marco doesn’t have the Cashier role."])

    def test_publish_tells_the_people_whose_shifts_changed(self):
        kept = Shift.objects.create(user=self.marco, role=self.cook, date=MON, start=T(7), end=T(15))
        gone = Shift.objects.create(user=self.marco, role=self.cook, date=MON, start=T(17), end=T(20), published=True,
                                    published_as={"user": self.marco.id})
        gone.deleted, gone.published = True, False
        gone.save()
        with mock.patch("misc_tools.email.send") as sent, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(schedule.publish(), 1)
        kept.refresh_from_db()
        self.assertTrue(kept.published)
        self.assertFalse(Shift.objects.filter(pk=gone.pk).exists())
        self.assertEqual(sent.call_args[0][:2], ("schedule_changed", "marco@example.com"))



class RequestTests(TestCase):
    """team/requests.py: what a request clashes with, and deciding it."""

    def setUp(self):
        self.cook = Role.objects.get_or_create(name="Cook")[0]
        self.marco = User.objects.create_user("marco", first_name="Marco", email="marco@example.com", is_staff=True)
        Staff.objects.create(user=self.marco).roles.add(self.cook)
        self.boss = User.objects.create_user("boss", first_name="Priya", is_staff=True)
        self.day = datetime.date.today() + datetime.timedelta(days=20)

    def test_approving_time_off_can_open_their_shifts(self):
        from . import requests as reqs
        clash = Shift.objects.create(user=self.marco, role=self.cook, date=self.day, start=T(13), end=T(21), published=True)
        Shift.objects.create(user=self.marco, role=self.cook, date=self.day, start=T(7), end=T(12), published=True)
        off = TimeOff.objects.create(user=self.marco, first_day=self.day, last_day=self.day, all_day=False, start=T(16), end=T(21))
        self.assertEqual(reqs.affected_shifts(off), [clash])  # the morning shift is fine
        with mock.patch("misc_tools.email.send") as sent, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(reqs.decide(off, True, self.boss, "Good luck!", "open"), 1)
        clash.refresh_from_db()
        off.refresh_from_db()
        self.assertEqual((clash.user, clash.published, off.status, off.handle, off.affected[0]["role"]), (None, False, "approved", "open", "Cook"))
        self.assertEqual(sent.call_args[0][2]["decision"], "approved")
        reqs.reopen(off)
        self.assertEqual(TimeOff.objects.get(pk=off.pk).status, "pending")

    def test_availability_changes_and_who_else_covers_the_role(self):
        from . import requests as reqs
        weekend = self.day + datetime.timedelta(days=(5 - self.day.weekday()) % 7)
        sat = Shift.objects.create(user=self.marco, role=self.cook, date=weekend, start=T(9), end=T(15))
        req = Availability.objects.create(user=self.marco, effective=self.day - datetime.timedelta(days=1),
                                          windows={str(d): ([[T(6), T(22)]] if d < 5 else []) for d in range(7)})
        self.assertEqual(reqs.affected_shifts(req), [sat])
        self.assertEqual(reqs.notes(req), [("bad", "Saturdays, Sundays would have no Cook. Nobody else has the Cook role.")])
        reqs.decide(req, False, self.boss)
        sat.refresh_from_db()
        self.assertEqual((sat.user, Availability.objects.get(pk=req.pk).status), (self.marco, "declined"))
