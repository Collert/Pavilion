"""
Push notifications about online orders (the Notifications page's "Order updates", push/kinds.py). Who hears: the
browsers linked to the order (a guest's, from its page: Order.push_subscriptions) and, for a customer's order, their
store subscriptions that want order updates.

Each change to an order or its delivery works out how far along it is (`stage`); a step further than the last one
notified (Order.push_stage) sends that step, once (a conditional update claims it, so two workers can't both send):
accepted (every station took it), ready for pickup, on its way (the courier picked it up), picked up, delivered.
Rejecting (the order is deleted) and refunding send their own.
"""
import datetime

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from deliveries.models import Delivery
from pos_server.models import Order, OrderStation
from push import kinds, send
from push.models import PushSubscription

ACCEPTED, READY, ON_THE_WAY, PICKED_UP, DELIVERED = 1, 2, 3, 4, 5
KIND = "order_updates"


def recipients(order):
    """The store subscriptions that hear about `order`."""
    mine = Q(orders=order)
    if order.customer_id:
        mine |= Q(user_id=order.customer_id, order_updates=True)
    return PushSubscription.objects.filter(mine, app=PushSubscription.STORE).distinct()


def stage(order) -> int:
    """How far along an online order is (0: nothing to tell yet)."""
    if order.channel not in ("web", "delivery") or order.voided_at:
        return 0
    statuses = [row.status for row in order.station_statuses.all()]
    if not statuses:
        return 0  # nothing to make (a gift card): no steps
    if order.channel == "delivery":
        delivery = order.delivery.order_by("-id").first()
        if delivery and delivery.delivered_at:
            return DELIVERED
        if delivery and delivery.picked_up_at:
            return ON_THE_WAY
    elif order.picked_up:
        return PICKED_UP
    if order.channel == "web" and all(s in (OrderStation.STATUS_DONE, OrderStation.STATUS_REJECTED) for s in statuses):
        return READY
    if OrderStation.STATUS_PENDING not in statuses:
        return ACCEPTED
    return 0


def _clock(moment) -> str:
    from online_store.storefront import clock
    return clock(timezone.localtime(moment).time())


def message(order, step, courier="") -> dict:
    """The notification for `step` of `order`, in the active language."""
    n = order.id
    scheduled = order.start_time > timezone.now() + datetime.timedelta(minutes=10)
    if step == ACCEPTED:
        title = _("Order #%(n)s accepted") % {"n": n}
        if scheduled:
            body = _("We’ll have it ready for %(time)s.") % {"time": _clock(order.start_time)}
        elif order.channel == "delivery":
            body = _("The kitchen is making it now. A courier brings it over once it’s ready.")
        else:
            body = _("The kitchen is making it now.")
    elif step == READY:
        title, body = _("Order #%(n)s is ready") % {"n": n}, _("It’s waiting for you on the pickup shelf.")
    elif step == ON_THE_WAY:
        title = _("Order #%(n)s is on its way") % {"n": n}
        body = (_("%(courier)s picked it up and is heading to you.") % {"courier": courier}) if courier else _("A courier picked it up and is heading to you.")
    elif step == PICKED_UP:
        title, body = _("Order #%(n)s picked up") % {"n": n}, _("Enjoy your meal!")
    else:
        title, body = _("Order #%(n)s delivered") % {"n": n}, _("Enjoy your meal!")
    return {"title": title, "body": body}


def _payload(order_id, text) -> dict:
    from online_store.app import _icon_url
    return {**text, "url": reverse("order_status", args=[order_id]), "tag": f"order-{order_id}", "icon": _icon_url()}


def check(order_id):
    """After a change: notify the order's next step if it reached one."""
    order = Order.objects.filter(pk=order_id).select_related("customer").first()
    if order is None or order.channel not in ("web", "delivery"):
        return
    subs = list(recipients(order))
    if not subs:
        return
    step = stage(order)
    if step <= order.push_stage:
        return
    if not Order.objects.filter(pk=order.pk, push_stage__lt=step).update(push_stage=step):
        return  # another worker sent it
    if not kinds.enabled(KIND):
        return
    courier = ""
    if step == ON_THE_WAY:
        delivery = order.delivery.select_related("courier").order_by("-id").first()
        courier = delivery.courier.first_name if delivery and delivery.courier else ""
    send.background(lambda: send.send_now(subs, lambda: _payload(order.id, message(order, step, courier)), urgency="high"))


def check_later(order_id):
    transaction.on_commit(lambda: check(order_id))


@receiver(post_save, sender=Order, dispatch_uid="order-push-order")
def order_saved(sender, instance, created, **kwargs):
    if not created and instance.channel in ("web", "delivery"):
        check_later(instance.pk)


@receiver(post_save, sender=Delivery, dispatch_uid="order-push-delivery")
def delivery_saved(sender, instance, **kwargs):
    if instance.order_id:
        check_later(instance.order_id)


@receiver(pre_delete, sender=Order, dispatch_uid="order-push-rejected")
def order_rejected(sender, instance, **kwargs):
    """The kitchen rejected (deleted) an order: tell whoever was following it, with the reason."""
    from online_store.models import RejectedOrder
    rejected = RejectedOrder.objects.filter(order_id=instance.id).first()
    if rejected is None or not kinds.enabled(KIND):
        return
    subs = list(recipients(instance))  # before the links go with the order
    if not subs:
        return
    order_id, reason = instance.id, rejected.reason

    def build():
        return _payload(order_id, {"title": _("We couldn’t take order #%(n)s") % {"n": order_id},
                                   "body": reason or _("Sorry about that. Nothing was charged.")})
    send.notify(subs, build, urgency="high")


def refunded(order, refund):
    """A refund was issued (pos_server/refunds.py)."""
    if order.channel not in ("web", "delivery") or not kinds.enabled(KIND):
        return
    amount = f"${refund.amount:.2f}"

    def build():
        return _payload(order.id, {"title": _("Refund for order #%(n)s") % {"n": order.id},
                                   "body": _("%(amount)s is on its way back to you.") % {"amount": amount}})
    send.notify(recipients(order), build)
