"""
Notifications (no artboard; laid out like Email): the push notifications Pavilion sends (push/kinds.py) by group, each
on or off (push.NotificationSwitch), plus how many devices have them on and a test notification to your own devices,
and the listmonk messenger that sends push campaigns (push/listmonk.py): the address listmonk posts to, its sign-in,
and setting it up in listmonk. The switches and the address are a draft saved with Save changes; the test, setting up,
a new password and showing it happen straight away.

    request.session["admin_notifications"] = {"on": {kind: bool}, "cat": group, "url": messenger address, "show": bool}
"""
import re

from django.db.models import Count, Q
from django.urls import reverse
from django.utils.translation import gettext as _

from push import listmonk as push_listmonk, send, vapid
from push.kinds import BY_KEY, GROUPS, KINDS
from push.models import NotificationSwitch, PushSubscription

KEY = "admin_notifications"
URL = re.compile(r"^https?://[^\s/]+(:\d+)?(/\S*)?$")


def saved() -> dict:
    switches = dict(NotificationSwitch.objects.values_list("kind", "enabled"))
    return {k.key: switches.get(k.key, True) for k in KINDS}


def state(request) -> dict:
    st = request.session.get(KEY)
    if not st or set(st.get("on", {})) != set(BY_KEY) or "url" not in st:
        st = {"on": saved(), "cat": GROUPS[0][0], "url": push_listmonk.messenger_url(request), "show": False}
    return st


def take_post(st, post):
    if "messenger_url" in post:
        st["url"] = post.get("messenger_url", "").strip()[:200]


def apply_op(st, op, request) -> tuple[str, str]:
    """(toast, error) for one button."""
    name, _sep, arg = op.partition(":")
    if name == "cat" and arg in dict((g[0], 1) for g in GROUPS):
        st["cat"] = arg
    elif name == "flip" and arg in BY_KEY:
        st["on"][arg] = not st["on"].get(arg, True)
    elif name == "showpass":
        st["show"] = not st.get("show")
    elif name == "newpass":
        push_listmonk.new_password()
        st["show"] = True
        return _("New password made. Set the messenger up in listmonk again so it signs in with it."), ""
    elif name == "messenger":
        from misc_tools import listmonk
        if not URL.match(st["url"]):
            return "", _("Give the address listmonk can reach Pavilion at, starting with https:// (or http:// on your own network).")
        try:
            push_listmonk.install(st["url"])
        except listmonk.ListmonkError as e:
            return "", str(e)
        return _("The messenger is set up in listmonk (it reloads itself, or after its running campaigns). Pick “%(name)s” as a campaign’s messenger.") % {"name": push_listmonk.NAME}, ""
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
    from admin_portal.models import StoreSettings
    for kind, on in st["on"].items():
        if kind in BY_KEY:
            NotificationSwitch.objects.update_or_create(kind=kind, defaults={"enabled": bool(on)})
    store = StoreSettings.load()
    if URL.match(st.get("url", "")) and store.push_messenger_url != st["url"]:
        store.push_messenger_url = st["url"]
        store.save(update_fields=["push_messenger_url"])


def saved_url(request) -> str:
    return push_listmonk.messenger_url(request)


def page(st, request) -> dict:
    from misc_tools import listmonk
    counts = PushSubscription.objects.aggregate(
        news=Count("id", filter=Q(app=PushSubscription.STORE, news_updates=True, user__isnull=False)),
        store=Count("id", filter=Q(app=PushSubscription.STORE)),
        accounts=Count("id", filter=Q(app=PushSubscription.STORE, user__isnull=False)),
        events=Count("id", filter=Q(app=PushSubscription.STORE, event_updates=True)),
        couriers=Count("id", filter=Q(app=PushSubscription.DELIVERIES)))
    reach = {"order_updates": counts["store"], "event_updates": counts["events"], "campaigns": counts["news"]}
    connected = listmonk.is_configured()
    found = push_listmonk.status() if connected else {}
    messenger = {"connected": connected, "status": found, "name": push_listmonk.NAME, "user": push_listmonk.USER,
                 "password": push_listmonk.password() if st.get("show") else "", "url_ok": bool(URL.match(st.get("url", ""))),
                 "moved": bool(found.get("url")) and found.get("url") != st.get("url"),
                 "edit_url": listmonk.config()["url"] + "/admin/settings" if connected else ""}
    group = next((g for g in GROUPS if g[0] == st["cat"]), GROUPS[0])
    kinds = [k for k in KINDS if k.group == group[0]]
    return {
        "st": st, "dirty": st["on"] != saved() or st.get("url") != saved_url(request), "messenger": messenger,
        "groups": [{"key": key, "label": label, "icon": icon, "on": key == group[0], "count": sum(1 for k in KINDS if k.group == key)}
                   for key, label, icon in GROUPS],
        "group": {"key": group[0], "label": group[1], "count": len(kinds)},
        "kinds": [{"k": k, "on": st["on"].get(k.key, True), "reach": reach.get(k.key)} for k in kinds],
        "facts": [(_("Store devices"), counts["store"]), (_("With an account"), counts["accounts"]),
                  (_("Event updates on"), counts["events"]), (_("Courier devices"), counts["couriers"])],
        "key": vapid.public_key()[:12] + "…",
    }
