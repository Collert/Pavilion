"""
When menus are live (design: AdminMenus, schedule tab).

A menu is live when it isn't a draft and either it's switched by hand and turned on, or it's automatic and its
rules say so. Rules (MenuRule) turn it on or off: every week on some days at a time, between two dates, or (off
only) when another menu turns on. Weekly rules are worked out as on/off segments over a week, in minutes from
Monday 00:00; dates rules apply on top (an "on" date range keeps it on all day, an "off" one keeps it off).
Several menus can be live at once; the register and online store list them by priority (Menu.position).
"""
import datetime

from django.utils import timezone
from django.utils.translation import gettext as _

WEEK = 7 * 1440
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def minute_of_week(when) -> int:
    when = timezone.localtime(when)
    return when.weekday() * 1440 + when.hour * 60 + when.minute


def _weekly_events(menu, rules_by_menu, menus_by_id):
    events = []
    for rule in rules_by_menu.get(menu.id, []):
        if rule.type == rule.WEEKLY and rule.time is not None:
            minute = rule.time.hour * 60 + rule.time.minute
            for day in rule.day_list:
                events.append((day * 1440 + minute, 1 if rule.kind == rule.ON else 0))
        elif rule.type == rule.MENU and rule.kind == rule.OFF and rule.other_menu_id:
            other = menus_by_id.get(rule.other_menu_id)
            if other and other.auto:
                for other_rule in rules_by_menu.get(other.id, []):
                    if other_rule.kind == other_rule.ON and other_rule.type == other_rule.WEEKLY and other_rule.time is not None:
                        minute = other_rule.time.hour * 60 + other_rule.time.minute
                        events.extend((day * 1440 + minute, 0) for day in other_rule.day_list)
    return events


def segments(menu, rules_by_menu, menus_by_id):
    """[(start, end)] minutes of the week when the menu's weekly rules have it on. Empty if it isn't automatic."""
    if not menu.auto:
        return []
    events = sorted(_weekly_events(menu, rules_by_menu, menus_by_id), key=lambda e: (e[0], -e[1]))
    if not events:
        return []
    on = events[-1][1] == 1  # the state at the end of the week carries into Monday
    start = 0 if on else None
    out = []
    for minute, turn_on in events:
        if turn_on and start is None:
            start = minute
        elif not turn_on and start is not None:
            if minute > start:
                out.append((start, minute))
            start = None
    if start is not None:
        out.append((start, WEEK))
    return out


class Schedule:
    """Every menu's rules loaded once, to answer 'is it live' and 'what changes next' for many menus."""

    def __init__(self, menus=None):
        from .models import Menu, MenuRule
        self.menus = list(menus if menus is not None else Menu.objects.all())
        self.by_id = {m.id: m for m in self.menus}
        self.rules = {}
        for rule in MenuRule.objects.filter(menu__in=[m for m in self.menus if m.pk]).order_by("id"):  # (a new menu being edited has none)
            self.rules.setdefault(rule.menu_id, []).append(rule)

    def segments(self, menu):
        return segments(menu, self.rules, self.by_id)

    def date_rules(self, menu, kind):
        return [r for r in self.rules.get(menu.id, []) if r.type == r.DATES and r.kind == kind and r.start_date and r.end_date]

    def is_live(self, menu, now=None):
        if menu.draft:
            return False
        if not menu.auto:
            return menu.manual_on
        now = timezone.localtime(now or timezone.now())
        today = now.date()
        if any(r.start_date <= today <= r.end_date for r in self.date_rules(menu, "off")):
            return False
        if any(r.start_date <= today <= r.end_date for r in self.date_rules(menu, "on")):
            return True
        minute = minute_of_week(now)
        return any(a <= minute < b for a, b in self.segments(menu))

    def live(self, now=None):
        return [m for m in sorted(self.menus, key=lambda m: (m.position, m.id or 0)) if self.is_live(m, now)]

    def next_change(self, menu, now=None):
        """'Turns off today at 9:00 PM' style text for the next weekly change, or ''."""
        segs = self.segments(menu)
        if not segs:
            return ""
        now = timezone.localtime(now or timezone.now())
        minute = minute_of_week(now)
        points = [(a, "on") for a, b in segs] + [(b, "off") for a, b in segs]
        points += [(m + WEEK, k) for m, k in points]
        upcoming = sorted((m, k) for m, k in points if m > minute and m % WEEK != 0)
        if not upcoming:
            return ""
        at, kind = upcoming[0]
        day = (at // 1440) % 7
        today_idx = minute // 1440
        when = (_("today") if at < (today_idx + 1) * 1440 else _("tomorrow") if day == (today_idx + 1) % 7 else _(DAY_NAMES[day]))
        clock = fmt_minutes(at % 1440)
        return (_("Turns on %(when)s at %(time)s") if kind == "on" else _("Turns off %(when)s at %(time)s")) % {"when": when, "time": clock}


def fmt_minutes(minute):
    h, m = divmod(minute % 1440, 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def days_text(days):
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days = sorted(set(days))
    if not days:
        return _("No days")
    if len(days) == 7:
        return _("Daily")
    if len(days) >= 3 and days == list(range(days[0], days[-1] + 1)):
        return f"{_(names[days[0]])}–{_(names[days[-1]])}"
    return ", ".join(_(names[d]) for d in days)


def live_menus(now=None):
    return Schedule().live(now)
