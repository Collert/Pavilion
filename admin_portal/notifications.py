"""
Notifications (no artboard; laid out like Email): the push notifications Pavilion sends (push/kinds.py) by group, each
on or off (push.NotificationSwitch), plus how many devices have them on and a test notification to your own devices.
The switches are a draft saved with Save changes; the test goes straight away.

    request.session["admin_notifications"] = {"on": {kind: bool}, "cat": group}
"""
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.translation import gettext as _

from push import send, vapid
from push.kinds import BY_KEY, GROUPS, KINDS
from push.models import NotificationSwitch, PushSubscription

KEY = "admin_notifications"


def saved() -> dict:
    switches = dict(NotificationSwitch.objects.values_list("kind", "enabled"))
    return {k.key: switches.get(k.key, True) for k in KINDS}


def state(session) -> dict:
    st = session.get(KEY)
    if not st or set(st.get("on", {})) != set(BY_KEY):
        st = {"on": saved(), "cat": GROUPS[0][0]}
    return st


def apply_op(st, op, request) -> tuple[str, str]:
    """(toast, error) for one button."""
    name, _sep, arg = op.partition(":")
    if name == "cat" and arg in dict((g[0], 1) for g in GROUPS):
        st["cat"] = arg
    elif name == "flip" and arg in BY_KEY:
        st["on"][arg] = not st["on"].get(arg, True)
    elif name == "test":
        mine = list(PushSubscription.objects.filter(user=request.user))
        if not mine:
            return "", _("None of your devices have notifications on yet. Turn them on in the store (your account’s Notifications page) or the courier app first.")
        name = request.user.first_name or request.user.username
        sent = send.send_now(mine, lambda: {"title": _("Test notification"), "body": _("Hi %(name)s! Notifications reach this device.") % {"name": name},
                                            "url": reverse("store_home"), "tag": "test"})
        if not sent:
            return "", _("The push service didn’t take it. Devices it refused for good were removed.")
        return _("Sent to %(n)d of your devices.") % {"n": sent}, ""
    return "", ""


def save(st):
    for kind, on in st["on"].items():
        if kind in BY_KEY:
            NotificationSwitch.objects.update_or_create(kind=kind, defaults={"enabled": bool(on)})


def page(st) -> dict:
    counts = PushSubscription.objects.aggregate(
        store=Count("id", filter=Q(app=PushSubscription.STORE)),
        accounts=Count("id", filter=Q(app=PushSubscription.STORE, user__isnull=False)),
        events=Count("id", filter=Q(app=PushSubscription.STORE, event_updates=True)),
        couriers=Count("id", filter=Q(app=PushSubscription.DELIVERIES)))
    reach = {"order_updates": counts["store"], "event_updates": counts["events"]}
    group = next((g for g in GROUPS if g[0] == st["cat"]), GROUPS[0])
    kinds = [k for k in KINDS if k.group == group[0]]
    return {
        "st": st, "dirty": st["on"] != saved(),
        "groups": [{"key": key, "label": label, "icon": icon, "on": key == group[0], "count": sum(1 for k in KINDS if k.group == key)}
                   for key, label, icon in GROUPS],
        "group": {"key": group[0], "label": group[1], "count": len(kinds)},
        "kinds": [{"k": k, "on": st["on"].get(k.key, True), "reach": reach.get(k.key)} for k in kinds],
        "facts": [(_("Store devices"), counts["store"]), (_("With an account"), counts["accounts"]),
                  (_("Event updates on"), counts["events"]), (_("Courier devices"), counts["couriers"])],
        "key": vapid.public_key()[:12] + "…",
    }
