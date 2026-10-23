import datetime
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
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



class TimeClockTests(TestCase):
    """team/clock.py: clocking in on time, early or off the schedule, breaks and clocking out."""

    def setUp(self):
        from users.testing import crew
        self.cook = Role.objects.get_or_create(name="Cook")[0]
        self.marco = User.objects.create_user("marco", first_name="Marco", is_staff=True)
        Staff.objects.create(user=self.marco).roles.add(self.cook)
        self.boss = crew(User.objects.create_user("boss", first_name="Priya", is_staff=True), "act.clock")
        self.day = timezone.localdate()

    def at(self, hour, minute=0):
        return timezone.make_aware(datetime.datetime.combine(self.day, datetime.time(hour, minute)))

    def test_on_time_breaks_and_out(self):
        from . import clock
        from .models import TimeEntry
        shift = Shift.objects.create(user=self.marco, role=self.cook, date=self.day, start=T(9), end=T(17), published=True)
        self.assertTrue(clock.check_in(self.marco, self.at(8, 15))["ok"])  # within the hour before
        entry = clock.clock_in(self.marco, self.at(8, 15))
        self.assertEqual((entry.shift, entry.added_shift, clock.state(self.marco)), (shift, False, "in"))
        clock.start_break(self.marco, self.at(12))
        self.assertEqual(clock.state(self.marco), "break")
        with self.assertRaises(clock.ClockError):
            clock.start_break(self.marco, self.at(12, 5))
        clock.end_break(self.marco, self.at(12, 30))
        entry = clock.clock_out(self.marco, self.at(17))
        self.assertEqual(entry.worked_seconds() // 60, 8 * 60 + 45 - 30)  # 8:15 to 5:00, less the break
        self.assertEqual(Staff.objects.get(user=self.marco).minutes_worked, 495)
        self.assertEqual(clock.state(self.marco), "out")
        self.assertEqual([p["kind"] for p in clock.today(self.marco, self.at(18))], ["in", "break", "out"])
        self.assertEqual(TimeEntry.objects.count(), 1)

    def test_early_and_off_the_schedule_need_a_manager(self):
        from . import clock
        Shift.objects.create(user=self.marco, role=self.cook, date=self.day, start=T(14), end=T(20), published=True)
        plan = clock.check_in(self.marco, self.at(11))
        self.assertFalse(plan["ok"])
        self.assertIn("more than an hour early", plan["reason"])
        with self.assertRaises(clock.ClockError):
            clock.clock_in(self.marco, self.at(11))
        entry = clock.clock_in(self.marco, self.at(11), approver=self.boss)
        self.assertEqual((entry.approved_by, entry.added_shift, entry.shift.start), (self.boss, False, T(14)))
        clock.clock_out(self.marco, self.at(20))
        # Not on the schedule: approved, and a shift is added that follows what happened
        other = User.objects.create_user("ana", first_name="Ana", is_staff=True)
        Staff.objects.create(user=other).roles.add(self.cook)
        self.assertIn("not on the schedule", clock.check_in(other, self.at(10, 3))["reason"])
        entry = clock.clock_in(other, self.at(10, 3), approver=self.boss)
        self.assertTrue(entry.added_shift)
        self.assertEqual((entry.shift.start, entry.shift.role, entry.shift.published), (T(10), self.cook, True))
        clock.clock_out(other, self.at(13, 40))
        entry.shift.refresh_from_db()
        self.assertEqual(entry.shift.end, T(13, 40))

    def test_staff_cards_refill_each_shift_and_can_need_the_clock(self):
        from decimal import Decimal
        from gift_cards import staff_cards
        from gift_cards.models import StaffCard, StaffCardType
        from . import clock
        t = StaffCardType.objects.create(name="Shift meal", amount=Decimal("12"), refill="shift", on_shift=True, channels=["store"])
        card = StaffCard.objects.create(type=t, user=self.marco, number="1234123412341234", balance=Decimal("3"))
        self.assertIn("only while Marco is clocked in", staff_cards.problem(card, "store"))
        Shift.objects.create(user=self.marco, role=self.cook, date=self.day, start=0, end=1440, published=True)
        clock.clock_in(self.marco)
        card.refresh_from_db()
        self.assertEqual(card.balance, Decimal("12.00"))
        self.assertEqual(staff_cards.problem(card, "store"), "")


@override_settings(ENFORCE_DEVICE_CHECK=False)
class TimeClockScreenTests(TestCase):
    """The time clock screen (team/clock_views.py): who's clocking in, their clock, a manager's approval."""

    def setUp(self):
        from users.testing import crew
        self.url = reverse("time_clock")
        self.marco = User.objects.create_user("marco", first_name="Marco", email="marco@example.com", password="secret-pass", is_staff=True)
        self.staff = Staff.objects.create(user=self.marco)
        self.staff.set_pin("1234")
        self.staff.save()
        boss = crew(User.objects.create_user("boss", first_name="Priya", is_staff=True), "act.clock")
        self.manager = Staff.objects.create(user=boss)
        self.manager.set_pin("2468")
        self.manager.save()

    def test_pin_off_the_schedule_with_a_managers_ok(self):
        from .models import TimeEntry
        page = self.client.get(self.url)
        self.assertEqual(page.context["view"], "who")
        self.assertEqual({p["name"] for p in page.context["people"]}, {"Marco", "Priya"})
        self.client.post(self.url, {"op": "pin", "staff": self.staff.id, "pin": "0000"})
        self.assertEqual(self.client.get(self.url).context["error"], "That PIN didn’t match. Try again.")
        self.client.post(self.url, {"op": "pin", "staff": self.staff.id, "pin": "1234"})
        page = self.client.get(self.url)
        self.assertEqual((page.context["view"], page.context["me"]["state"]), ("me", "out"))
        self.client.post(self.url, {"op": "in"})
        page = self.client.get(self.url)
        self.assertEqual(page.context["view"], "approve")
        self.assertEqual([a["name"] for a in page.context["approvers"]], ["Priya"])
        self.client.post(self.url, {"op": "approve", "staff": self.manager.id, "pin": "2468"})
        page = self.client.get(self.url)
        self.assertEqual(page.context["view"], "done")
        self.assertContains(page, "approved by Priya")
        entry = TimeEntry.objects.get()
        self.assertEqual((entry.user, entry.approved_by.first_name, entry.added_shift), (self.marco, "Priya", True))
        self.client.post(self.url, {"op": "done"})
        self.assertEqual(self.client.get(self.url).context["board"]["on"][0]["name"], "Marco")

    def test_people_who_approve_clock_ins_approve_their_own(self):
        from .models import TimeEntry
        owner = User.objects.create_superuser("alex", password="x", first_name="Alex")
        profile = Staff.objects.create(user=owner)
        profile.set_pin("0000")
        profile.save()
        self.client.post(self.url, {"op": "pin", "staff": profile.id, "pin": "0000"})
        page = self.client.get(self.url)
        self.assertTrue(page.context["me"]["plan"]["self"])
        self.assertContains(page, "You can approve clock-ins, so you can go ahead")
        self.client.post(self.url, {"op": "in"})
        self.assertEqual(self.client.get(self.url).context["view"], "done")
        entry = TimeEntry.objects.get(user=owner)
        self.assertEqual((entry.approved_by, entry.added_shift), (owner, True))

    def test_email_and_password_then_break_and_out(self):
        Shift.objects.create(user=self.marco, date=timezone.localdate(), start=0, end=1440, published=True)
        self.client.post(self.url, {"op": "tab:email"})
        self.client.post(self.url, {"op": "password", "email": "marco@example.com", "password": "wrong"})
        self.assertIn("didn’t match", self.client.get(self.url).context["error"])
        self.client.post(self.url, {"op": "password", "email": "MARCO@example.com", "password": "secret-pass"})
        self.client.post(self.url, {"op": "in"})
        self.assertContains(self.client.get(self.url), "You’re clocked in, Marco")
        self.client.post(self.url, {"op": "done"})
        self.client.post(self.url, {"op": "pin", "staff": self.staff.id, "pin": "1234"})
        self.client.post(self.url, {"op": "break"})
        self.client.post(self.url, {"op": "done"})
        self.client.post(self.url, {"op": "pin", "staff": self.staff.id, "pin": "1234"})
        self.assertEqual(self.client.get(self.url).context["me"]["state"], "break")
        self.client.post(self.url, {"op": "out"})
        self.assertContains(self.client.get(self.url), "You’re clocked out, Marco")
        self.assertFalse("_auth_user_id" in self.client.session)  # never signed in
