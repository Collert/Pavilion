"""
Sending Pavilion's events to every connected Switchboard (protocol §4).

    events.order_placed(order)        # pos_server.orders.create_order: every channel
    events.order_ready(order)         # pos_server.signals.order_ready: the last station finished it
    events.clocked_in(entry)          # team/clock.py
    events.clocked_out(entry, now)
    ...                               # one function per trigger in manifest.py, called from the one place it happens

Where each is called from is said next to it below. A few come from model signals instead (hooks.py: gift card
history, staff cards, cash movements, store events, service bookings), because the row being written is the event.

These never raise and cost the caller nothing but an on-commit hook: after the transaction commits, one query checks
for connections (none: nothing more happens); with some, the event is written to the delivery log and posted from a
background thread, signed with each connection's webhook secret. A failed delivery (anything but 2xx, a timeout, a
network error) is tried up to ATTEMPTS times with growing waits; a 410 marks the connection broken and stops. What is
due is in the database (Delivery.next_attempt_at), not in memory: deliveries a restart left behind are picked up with
the next event (sweep).

Tests replace the three seams: `post` (the only outgoing HTTP), `background` and `wait`.
"""
import hashlib
import hmac
import json
import logging
import secrets as tokens
import threading
import datetime
import time
from datetime import timedelta
from functools import wraps

from django.db import close_old_connections, transaction
from django.utils import timezone

from payments import secrets

from .connections import BUILDING
from .contract import LIST_LIMIT
from .models import LOG_DAYS, Delivery, SwitchboardConnection

logger = logging.getLogger(__name__)

ATTEMPTS = 5
WAITS = (5, 30, 120, 600)  # seconds before the 2nd, 3rd, 4th and 5th try
TIMEOUT = 10
USER_AGENT = "Pavilion/1.0 (Switchboard)"
STALE = timedelta(minutes=2)  # a delivery this far past its next try has lost its thread (a restart)


# ---------- seams ----------

def post(url, body: bytes, headers: dict, timeout=TIMEOUT) -> int:
    """POST the signed event; the HTTP status it got. The only place Pavilion calls a Switchboard."""
    import requests
    return requests.post(url, data=body, headers=headers, timeout=timeout, allow_redirects=False).status_code


def wait(seconds):
    time.sleep(seconds)


def background(job):
    """Run `job` on its own thread with its own database connection."""
    def run():
        close_old_connections()
        try:
            job()
        except Exception:
            logger.exception("Sending a Switchboard event failed")
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True, name="pavilion-switchboard").start()


# ---------- emitting ----------

def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def emit(event_type, build, at=None):
    """Send `event_type` once the current transaction commits. `build()` returns (subject, data, summary), a list of
    them (one event each) or None (nothing to say after all), and is only called when a Switchboard is connected.
    Never raises: an event must not break what caused it."""
    emit_all([(event_type, build)], at)


def emit_all(events, at=None):
    """Several events from one occurrence, [(event_type, build)], for the price of one: a single look for connections."""
    try:
        at = at or timezone.now()
        transaction.on_commit(lambda: _committed(events, at), robust=True)
    except Exception:
        logger.exception("Couldn't queue the Switchboard events %s", [event_type for event_type, _build in events])


def emit_now(event_type, build, at=None):
    """Like emit, for something that is about to be gone (a rejected order is deleted): `build()` runs straight away,
    while it is still there, and only when a Switchboard is connected (one query)."""
    try:
        if not SwitchboardConnection.objects.filter(broken_at__isnull=True).exists():
            return
        built = build()
        emit(event_type, lambda: built, at)
    except Exception:
        logger.exception("Couldn't build the Switchboard event %s", event_type)


def safe(trigger):
    """A trigger function that can never break its caller."""
    @wraps(trigger)
    def wrapper(*args, **kwargs):
        try:
            trigger(*args, **kwargs)
        except Exception:
            logger.exception("The Switchboard event %s failed", trigger.__name__)
    return wrapper


def _committed(events, at):
    try:
        connections = list(SwitchboardConnection.objects.filter(broken_at__isnull=True))
    except Exception:
        logger.exception("Couldn't look for connected Switchboards")
        return
    if not connections:
        return
    ids = []
    for event_type, build in events:
        try:  # one event that can't be built doesn't take the others with it
            built = build()
            for subject, data, summary in (built if isinstance(built, list) else [built] if built else []):
                event_id = "evt_" + tokens.token_hex(10)
                for connection in connections:
                    envelope = {"id": event_id, "type": event_type,
                                "source": {"building": BUILDING, "instance": connection.base_url},
                                "occurred_at": timezone.localtime(at).isoformat(timespec="seconds"), "subject": subject, "data": data}
                    ids.append(Delivery.objects.create(connection=connection, event_id=event_id, event_type=event_type, summary=summary[:140],
                                                       body=json.dumps(envelope, ensure_ascii=False), created_at=at, next_attempt_at=at).pk)
        except Exception:
            logger.exception("Couldn't send the Switchboard event %s", event_type)
    if ids:
        try:
            background(lambda: _send(ids))
        except Exception:
            logger.exception("Couldn't start sending Switchboard events")


def _send(ids):
    for delivery_id in ids[1:]:
        background(lambda delivery_id=delivery_id: follow(delivery_id))  # one slow Switchboard doesn't hold up the others
    sweep(skip=ids)
    follow(ids[0])


def follow(delivery_id):
    """Try a delivery until it's over, waiting between tries."""
    while True:
        delay = attempt(delivery_id)
        if delay is None:
            return
        close_old_connections()  # not held while sleeping
        wait(delay)


def attempt(delivery_id):
    """One try. Returns the seconds to wait before the next, or None when the delivery is over (or someone else's)."""
    delivery = Delivery.objects.select_related("connection").filter(pk=delivery_id, status=Delivery.PENDING).first()
    if delivery is None:
        return None
    connection = delivery.connection
    if connection.broken:
        _finish(delivery, Delivery.GONE)
        return None
    if not _claim(delivery):
        return None
    body = delivery.body.encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "X-Switchboard-Event": delivery.event_type,
               "X-Switchboard-Delivery": delivery.event_id, "X-Switchboard-Signature": sign(secrets.decrypt(connection.secret), body)}
    status, error = None, ""
    try:
        status = post(connection.webhook_url, body, headers, TIMEOUT)
    except Exception as e:  # a timeout or the network: a failed delivery like any other
        error = (str(e) or e.__class__.__name__)[:200]
    delivery.response_status, delivery.error = status, error
    now = timezone.now()
    if status is not None and 200 <= status < 300:
        SwitchboardConnection.objects.filter(pk=connection.pk).update(last_delivery_at=now)
        _finish(delivery, Delivery.DELIVERED)
    elif status == 410:  # Switchboard no longer knows this connection: stop, and say so on the admin's page
        SwitchboardConnection.objects.filter(pk=connection.pk, broken_at__isnull=True).update(broken_at=now)
        Delivery.objects.filter(connection=connection, status=Delivery.PENDING).exclude(pk=delivery.pk).update(
            status=Delivery.GONE, body="", finished_at=now, next_attempt_at=None)
        _finish(delivery, Delivery.GONE)
        logger.warning("Switchboard %s (%s) answered 410; the connection is marked broken", connection.pk, connection.host)
    elif delivery.attempts >= ATTEMPTS:
        _finish(delivery, Delivery.FAILED)
        logger.warning("Switchboard event %s to %s failed %s times (%s)", delivery.event_id, connection.host, ATTEMPTS, status or error)
    else:
        delay = WAITS[delivery.attempts - 1]
        delivery.next_attempt_at = now + timedelta(seconds=delay)
        delivery.save(update_fields=["response_status", "error", "next_attempt_at"])
        _changed()
        return delay
    return None


def _claim(delivery) -> bool:
    """Take the delivery's next try with a conditional update, so two workers never send the same one."""
    tried = delivery.attempts
    if not Delivery.objects.filter(pk=delivery.pk, status=Delivery.PENDING, attempts=tried).update(attempts=tried + 1):
        return False
    delivery.attempts = tried + 1
    return True


def _finish(delivery, status):
    delivery.status, delivery.body, delivery.finished_at, delivery.next_attempt_at = status, "", timezone.now(), None
    delivery.save(update_fields=["status", "body", "finished_at", "next_attempt_at", "response_status", "error"])
    _changed()


def _changed():
    from pavilion.realtime import publish
    publish("switchboard")  # the admin's Switchboard page refetches


def sweep(skip=()):
    """Housekeeping with each event: drop the old log, and take up deliveries whose thread is gone (a restart)."""
    now = timezone.now()
    Delivery.objects.filter(created_at__lt=now - timedelta(days=LOG_DAYS)).delete()
    stale = Delivery.objects.filter(status=Delivery.PENDING, next_attempt_at__lt=now - STALE).exclude(pk__in=list(skip))
    for delivery_id in list(stale.values_list("pk", flat=True)[:20]):
        background(lambda delivery_id=delivery_id: follow(delivery_id))


# ---------- Pavilion's events (manifest.py: TRIGGERS) ----------

def iso(moment) -> str:
    return timezone.localtime(moment).isoformat(timespec="seconds")


def money(value) -> float:
    return float(value or 0)


def number(value) -> float:
    """A quantity as JSON: whole numbers stay whole (800, not 800.0)."""
    value = round(float(value or 0), 3)
    return int(value) if value == int(value) else value


def full_name(user) -> str:
    return (user.get_full_name() or user.username) if user else ""


def _person(user) -> dict:
    return {"id": str(user.pk), "name": full_name(user), "email": user.email or ""}


def _subject(kind, pk, name="", email="") -> dict:
    return {"type": kind, "id": str(pk), "email": email or "", "name": name or ""}


# ----- orders -----

def order_lines(order) -> list:
    """The lines of an order (what was sold, as a receipt lists it: a combo is one line), in the order they were rung
    up. One query, or none when the order's items were prefetched (with their stations)."""
    prefetched = "items" in getattr(order, "_prefetched_objects_cache", {})
    rows = order.items.all() if prefetched else order.items.filter(parent__isnull=True).select_related("station")
    return [row for row in rows if row.parent_id is None]


def _line(row) -> dict:
    return {"dish_id": str(row.dish_id or ""), "name": row.title, "quantity": row.quantity, "unit_price": money(row.unit_price),
            "total": money(row.line_total), "station": row.station.name if row.station_id else ""}


def _order(order):
    customer = order.customer
    email = order.email or (customer.email if customer else "")
    name = order.name or (customer.get_full_name() if customer else "") or ""
    kind = {"web": "pickup", "delivery": "delivery"}.get(order.channel) or ("to_go" if order.to_go_order else "dine_in")
    number = f"{order.pk:04d}"
    lines = order_lines(order)
    subject = {"type": "order", "id": str(order.pk), "email": email, "name": name}
    data = {"order": {"id": str(order.pk), "number": number, "type": kind, "channel": order.channel, "total": float(order.total),
                      "items": int(sum(row.quantity for row in lines)), "subtotal": money(order.subtotal), "discount": money(order.discount),
                      "tax": money(order.tax), "tip": money(order.tip),
                      "summary": ", ".join(f"{row.quantity} × {row.title}" for row in lines)[:500], "placed_at": iso(order.timestamp),
                      "line_count": len(lines), "lines": [_line(row) for row in lines[:LIST_LIMIT]]},
            "customer": {"name": name, "email": email, "phone": str(order.phone or "")}}
    return subject, data, number


def _discounts(order):
    """One discount.used per discount on a new order."""
    rows = list(order.discounts.select_related("discount"))
    if not rows:
        return None
    subject, data, number = _order(order)
    staff = {use.amount for use in order.staff_card_uses.all() if use.kind == "discount"}
    out = []
    for row in rows:
        d = row.discount
        kind = ("coupon" if d.code else "automatic" if d.automatic else "offer") if d else ("staff_card" if row.amount in staff else "manual")
        used = {"id": str(d.pk) if d else "", "name": (d.name if d else row.label) or "", "code": (d.code or "") if d else "",
                "kind": kind, "amount": money(row.amount)}
        out.append((subject, {**data, "discount": used}, f"{used['name']} used on order {number}"))
    return out


@safe
def order_placed(order):
    """pos_server.orders.create_order, every channel. Its discounts each send discount.used."""
    def build():
        subject, data, number = _order(order)
        return subject, data, f"Order {number} placed"
    emit_all([("order.placed", build), ("discount.used", lambda: _discounts(order))], at=order.timestamp)


@safe
def order_ready(order):
    def build():
        subject, data, number = _order(order)
        return subject, data, f"Order {number} is ready"
    emit("order.ready", build)


@safe
def order_approved(order):
    """Order.set_station_status: the last pending station confirmed it."""
    def build():
        subject, data, number = _order(order)
        return subject, data, f"Order {number} approved"
    emit("order.approved", build)


@safe
def order_station_done(order, station_ids):
    """Order.set_station_status: these stations just finished their part."""
    def build():
        from pos_server.models import Station
        subject, data, number = _order(order)
        return [(subject, {**data, "station": {"code": s.code, "name": s.name}}, f"{s.name} finished order {number}")
                for s in Station.objects.filter(pk__in=station_ids)]
    emit("order.station_done", build)


@safe
def order_rejected(order, reason):
    """The kitchen screen's Reject (pos_server.views.order_marking), just before the order is deleted."""
    def build():
        subject, data, number = _order(order)
        return subject, {**data, "reason": reason}, f"Order {number} rejected"
    emit_now("order.rejected", build)


@safe
def order_picked_up(order):
    """The kitchen screen's Picked up, or the courier collecting a delivery order."""
    def build():
        subject, data, number = _order(order)
        return subject, data, f"Order {number} picked up"
    emit("order.picked_up", build)


@safe
def order_refunded(order, refund):
    """pos_server.refunds.refund_order."""
    def build():
        subject, data, number = _order(order)
        data["refund"] = {"id": str(refund.pk), "amount": money(refund.amount), "reason": refund.reason, "to": refund.to,
                          "whole": bool(refund.whole), "by": full_name(refund.by)}
        return subject, data, f"Order {number}: ${refund.amount:.2f} refunded"
    emit("order.refunded", build, at=refund.created_at)


# ----- deliveries (deliveries/views.py) -----

def _delivery(delivery, courier):
    subject, data, number = _order(delivery.order)
    data["staff"] = _person(courier)
    data["delivery"] = {"id": str(delivery.pk), "address": ", ".join(filter(None, [delivery.street, delivery.area]))}
    return subject, data, number


@safe
def delivery_assigned(delivery_id, courier):
    def build():
        from deliveries.models import Delivery as Trip
        trip = Trip.objects.select_related("order").filter(pk=delivery_id, order__isnull=False).first()
        if trip is None:
            return None
        subject, data, number = _delivery(trip, courier)
        return subject, data, f"{data['staff']['name']} took the delivery of order {number}"
    emit("delivery.assigned", build)


@safe
def delivery_picked_up(delivery):
    def build():
        subject, data, number = _delivery(delivery, delivery.courier)
        return subject, data, f"{data['staff']['name']} picked up order {number}"
    emit("delivery.picked_up", build, at=delivery.picked_up_at)


@safe
def delivery_delivered(delivery):
    def build():
        subject, data, number = _delivery(delivery, delivery.courier)
        start = delivery.picked_up_at or delivery.accepted_at or delivery.timestamp
        data["delivery"].update(handoff=delivery.handoff, minutes=max(0, round((delivery.delivered_at - start).total_seconds() / 60)))
        return subject, data, f"Order {number} delivered"
    emit("delivery.delivered", build, at=delivery.delivered_at)


# ----- catering (catering/flow.py; the deposit hold in catering/payments.py) -----

def _catering(order, catalog=None):
    """`catalog`: the catering.pricing.Catalog of several orders' lines, when a list of them is built (else it is loaded
    for this order, as its total always needed)."""
    from catering import pricing
    when = timezone.make_aware(datetime.datetime.combine(order.event_date, order.event_time))
    subject = _subject("catering_order", order.pk, order.name, order.email)
    lines = pricing.lines_of(order)
    catalog = catalog or pricing.Catalog(lines)
    listed = []
    for line in lines[:LIST_LIMIT]:
        figure = pricing.figure(line, catalog)
        listed.append({"kind": line["kind"], "name": str(figure["name"]), "quantity": int(line["quantity"]), "amount": int(line["amount"] or 0),
                       "total": money(figure["total"])})
    data = {"catering": {"id": str(order.pk), "number": order.number, "title": order.title, "status": order.status, "event_at": iso(when),
                         "fulfilment": order.fulfilment, "total": money(pricing.totals(lines, catalog)["total"]), "rush": bool(order.rush),
                         "line_count": len(lines), "lines": listed},
            "customer": {"name": order.name, "email": order.email, "phone": order.phone, "organization": order.organization}}
    return subject, data


@safe
def catering(event_type, order, summary, **more):
    """Every catering.* event: the order as it is once the change is saved, plus what that event adds."""
    def build():
        subject, data = _catering(order)
        return subject, {**data, **more}, f"{summary} {order.number}"
    emit(event_type, build)


@safe
def service_booked(booking):
    """A services.Booking was made (hooks.py): so far only by confirmed catering orders."""
    def build():
        from catering.models import CateringLine
        line = CateringLine.objects.filter(booking=booking).select_related("order").first()
        customer = booking.customer
        name = line.order.name if line else full_name(customer)
        email = line.order.email if line else (customer.email if customer else "")
        data = {"booking": {"id": str(booking.pk), "service": booking.service.title, "name": booking.name, "date": booking.date.isoformat(),
                            "amount": int(booking.amount), "count": int(booking.count), "total": money(booking.total),
                            "deposit": money(booking.deposit), "catering_number": line.order.number if line else ""},
                "customer": {"name": name, "email": email}}
        return _subject("booking", booking.pk, name, email), data, f"{booking.service.title} booked for {booking.name}"
    emit("service.booked", build)


# ----- gift cards and staff cards -----

def _gift(card):
    subject = _subject("gift_card", card.pk, card.recipient, card.email)
    data = {"card": {"id": str(card.pk), "last4": card.last4, "balance": money(card.available_balance),
                     "design": card.design.name if card.design else ""},
            "customer": {"name": card.recipient, "email": card.email}}
    return subject, data


@safe
def gift_card_history(event):
    """A line was added to a gift card's history (hooks.py; gift_cards.GiftCardEvent is written for every change of
    balance): sold or issued, used, loaded, and emptied. The card's number is never in any of them."""
    from gift_cards.models import GiftCard, GiftCardEvent
    card, amount, balance = event.card, event.amount, event.balance
    order_id = str(event.order_id or "")
    at = event.at

    def send(event_type, summary, **more):
        def build():
            subject, data = _gift(card)
            data["card"]["balance"] = money(balance)  # as it was right after this line of its history
            return subject, {**data, **more}, f"Gift card •• {card.last4} {summary}"
        emit(event_type, build, at=at)

    if event.kind == GiftCardEvent.ISSUE:
        if card.source == GiftCard.STAFF:
            send("gift_card.issued", "issued", amount=money(amount), issued_by=card.buyer or full_name(event.by))
        else:
            send("gift_card.sold", "sold", amount=money(amount), source="register" if card.source == GiftCard.REGISTER else "online",
                 buyer=card.buyer, order_id=order_id)
    elif event.kind == GiftCardEvent.USE:
        send("gift_card.used", "used", amount=money(-amount), order_id=order_id)
    elif event.kind in (GiftCardEvent.REFUND, GiftCardEvent.ADJUST) and amount > 0:
        send("gift_card.loaded", "loaded", amount=money(amount), reason="refund" if event.kind == GiftCardEvent.REFUND else "adjustment",
             note=event.note)
    if event.kind in (GiftCardEvent.USE, GiftCardEvent.ADJUST) and amount < 0 and balance <= 0:
        send("gift_card.emptied", "is empty")


def _staff_card(card):
    staff = _person(card.user)
    t = card.type
    return ({"type": "person", **staff},
            {"staff": staff, "card": {"id": str(card.pk), "label": t.name, "kind": t.kind, "balance": money(card.balance)}})


@safe
def staff_card_issued(card):
    """A gift_cards.StaffCard was made (hooks.py): on the Staff cards page or by Switchboard's own action."""
    def build():
        subject, data = _staff_card(card)
        return subject, data, f"{subject['name']} got a {card.type.name} card"
    emit("staff_card.issued", build, at=card.issued_at)


@safe
def staff_card_refilled(card):
    """gift_cards/staff_cards.py: refill (its schedule) and clocked_in ("every shift" cards)."""
    def build():
        subject, data = _staff_card(card)
        return subject, data, f"{subject['name']}'s {card.type.name} card refilled"
    emit("staff_card.refilled", build, at=card.refilled_at)


# ----- menu and stock (pos_server/models.py tells these when a dish, component or ingredient is saved) -----

@safe
def dish_availability(dish, sellable):
    """Dish.save: the dish itself stopped being sellable, or can be sold again (its station's switch is station.*)."""
    reason = "" if sellable else "turned_off" if dish.availability == "off" else "part_turned_off" if dish.blocked else "out_of_stock"

    def build():
        data = {"dish": {"id": str(dish.pk), "name": dish.title, "station": dish.station.name, "price": money(dish.price)}}
        if not sellable:
            data["reason"] = reason
        return _subject("dish", dish.pk, dish.title), data, f"{dish.title} is {'back' if sellable else 'sold out'}"
    emit("dish.back_in_stock" if sellable else "dish.sold_out", build)


@safe
def station_switched(station, taking):
    """A station's "taking tickets" switch changed (the admin's Stations page, or Switchboard's own action)."""
    code, name, pk = station.code, station.name, station.pk

    def build():
        return (_subject("station", pk, name), {"station": {"code": code, "name": name}},
                f"{name} {'is taking tickets again' if taking else 'stopped taking tickets'}")
    emit("station.resumed" if taking else "station.paused", build)


def _stock(obj, on_hand):
    from pos_server.models import Ingredient
    kind = "ingredient" if isinstance(obj, Ingredient) else "component"
    item = {"kind": kind, "id": str(obj.pk), "name": obj.title, "on_hand": number(on_hand), "unit": obj.unit_of_measurement}
    if obj.par is not None:
        item["par"] = number(obj.par)
    return _subject(kind, obj.pk, obj.title), {"item": item}


@safe
def stock_level(obj, was, now):
    """An ingredient's or component's count went from `was` to `now`: below its par, or down to nothing. Each fires
    once per crossing. Things that aren't counted (tap water, self-crafting components) never send these."""
    if was is None or now is None or now >= was or getattr(obj, "unlimited", False) or getattr(obj, "crafting_option", "") == "auto":
        return
    par = obj.par or 0
    if par > 0 and was >= par > now and now > 0:  # all the way to nothing is stock.ran_out
        emit("stock.low", lambda: (*_stock(obj, now), f"{obj.title} is below par"))
    if was > 0 >= now:
        emit("stock.ran_out", lambda: (*_stock(obj, now), f"{obj.title} ran out"))


@safe
def stock_drawn(model, pk, amount):
    """`amount` was taken off a count with a queryset update (packaging counted off an order), which no save() sees.
    Which event it is, if any, is only known once the count is read, after the commit."""
    def crossing(which):
        obj = model.objects.filter(pk=pk).first()
        if obj is None or obj.inventory is None or getattr(obj, "unlimited", False):
            return None
        now, was, par = obj.inventory, obj.inventory + amount, obj.par or 0
        happened = "ran_out" if was > 0 >= now else "low" if par > 0 and was >= par > now else ""
        if happened != which:
            return None
        return (*_stock(obj, now), f"{obj.title} {'ran out' if which == 'ran_out' else 'is below par'}")
    emit_all([("stock.ran_out", lambda: crossing("ran_out")), ("stock.low", lambda: crossing("low"))])


@safe
def shopping_logged(update):
    """inventory.views.log_shopping."""
    def build():
        rows = list(update.updateingredient_set.select_related("ingredient").order_by("id"))
        text = ", ".join(f"{r.ingredient.title} ({number(r.quantity)} {r.ingredient.unit_of_measurement})" for r in rows)[:500]
        bought = [{"item_id": str(r.ingredient_id), "name": r.ingredient.title, "quantity": number(r.quantity),
                   "unit": r.ingredient.unit_of_measurement} for r in rows[:LIST_LIMIT]]
        return (_subject("shopping", update.pk), {"update_id": str(update.pk), "items": len(rows), "summary": text, "ingredients": bought},
                f"Shopping logged: {len(rows)} ingredients")
    emit("stock.received", build)


@safe
def restock(event_type, order):
    """admin_portal/restocks.py: save_draft, when a purchase order turns placed, and when it is taken in as delivered."""
    def build():
        lines = list(order.lines.select_related("ingredient"))
        data = {"restock": {"id": str(order.pk), "number": order.number, "supplier": order.supplier.name, "total": money(order.total),
                            "lines": len(lines), "summary": ", ".join(f"{l.cases} × {l.ingredient.title}" for l in lines)[:500],
                            "expected_on": order.expected_on.isoformat() if order.expected_on else "",
                            "ingredients": [{"item_id": str(l.ingredient_id), "name": l.ingredient.title, "quantity": number(l.cases * l.pack_size),
                                             "unit": l.ingredient.unit_of_measurement, "cases": l.cases, "case_price": money(l.case_price),
                                             "total": money(l.total)} for l in lines[:LIST_LIMIT]]}}
        return _subject("restock", order.pk, order.number), data, f"{order.number} {event_type.split('.')[1]}"
    emit(event_type, build)


@safe
def batch_cooked(session, user, component, made):
    """inventory/cookery.py: finish."""
    def build():
        staff = _person(user)
        minutes = max(1, round((session.finished_at - session.started_at).total_seconds() / 60))
        data = {"staff": staff, "item": {"id": str(component.pk), "name": component.title, "unit": component.unit_of_measurement,
                                         "on_hand": number(component.inventory)},
                "made": number(made), "minutes": minutes}
        if session.expected:
            data["expected"] = number(session.expected)
        return {"type": "person", **staff}, data, f"{staff['name']} made {component.title}"
    emit("batch.cooked", build)


# ----- cash (pos_server/register.py: cash_drawer; movements from hooks.py) -----

def _drawer(drawer, user):
    staff = _person(user) if user else {"id": "", "name": "", "email": ""}
    data = {"staff": staff, "drawer": {"id": str(drawer.pk), "register": drawer.device.name if drawer.device else "Register",
                                       "opening": money(drawer.opening_amount)}}
    return {"type": "person", **staff}, data


@safe
def drawer_opened(drawer):
    def build():
        subject, data = _drawer(drawer, drawer.opened_by)
        return subject, data, f"{data['drawer']['register']} opened"
    emit("drawer.opened", build, at=drawer.opened_at)


@safe
def drawer_closed(drawer, cash_sales, orders):
    def build():
        subject, data = _drawer(drawer, drawer.closed_by)
        data["drawer"].update(expected=money(drawer.expected_amount), counted=money(drawer.counted_amount),
                              difference=money(drawer.difference), cash_sales=money(cash_sales), orders=int(orders))
        return subject, data, f"{data['drawer']['register']} closed"
    emit("drawer.closed", build, at=drawer.closed_at)


@safe
def cash_moved(movement):
    def build():
        subject, data = _drawer(movement.drawer, movement.user)
        return subject, {**data, "amount": money(movement.amount), "reason": movement.reason}, f"Cash moved at {data['drawer']['register']}"
    emit("drawer.cash_moved", build, at=movement.timestamp)


# ----- people -----

@safe
def customer_registered(user):
    """Someone made an account in the store (online_store.account.join, users.views.register)."""
    def build():
        from users.models import Customer
        profile = Customer.objects.filter(user=user).first()
        customer = {"id": str(user.pk), "name": full_name(user), "email": user.email or "",
                    "phone": str(profile.phone or "") if profile else "", "newsletter": bool(profile and profile.newsletter)}
        return {"type": "person", **_person(user)}, {"customer": customer}, f"{customer['name']} made an account"
    emit("customer.registered", build)


@safe
def staff_added(user):
    """admin_portal/staff.py: create and promote (the Staff page's Add, and Switchboard's staff.ensure)."""
    def build():
        from users.models import Staff
        staff = _person(user)
        profile = Staff.objects.filter(user=user).first()
        roles = ", ".join(profile.roles.filter(archived=False).values_list("name", flat=True)) if profile else ""
        return {"type": "person", **staff}, {"staff": staff, "roles": roles}, f"{staff['name']} was added to the staff"
    emit("staff.added", build)


def _staff(entry, at):
    user = entry.user
    name = user.get_full_name() or user.username
    role = entry.shift.role.name if entry.shift and entry.shift.role else ""
    staff = {"id": str(user.pk), "name": name, "email": user.email}
    return {"type": "person", **staff}, {"staff": staff, "at": timezone.localtime(at).isoformat(timespec="seconds"), "role": role}, name


@safe
def clocked_in(entry):
    def build():
        subject, data, name = _staff(entry, entry.clock_in)
        return subject, data, f"{name} clocked in"
    emit("staff.clocked_in", build, at=entry.clock_in)


@safe
def clocked_out(entry, now=None):
    at = entry.clock_out or now or timezone.now()

    def build():
        subject, data, name = _staff(entry, at)
        data["hours"] = round(entry.worked_seconds(at) / 3600, 2)
        return subject, data, f"{name} clocked out"
    emit("staff.clocked_out", build, at=at)


@safe
def break_started(entry, at):
    def build():
        subject, data, name = _staff(entry, at)
        return subject, data, f"{name} started a break"
    emit("staff.break_started", build, at=at)


@safe
def break_ended(entry, at):
    def build():
        subject, data, name = _staff(entry, at)
        ended = entry.breaks.filter(end=at).order_by("-start").first()
        data["minutes"] = max(0, round((at - ended.start).total_seconds() / 60)) if ended else 0
        return subject, data, f"{name} is back from a break"
    emit("staff.break_ended", build, at=at)


# ----- the schedule (team/schedule.py, team/swaps.py, team/requests.py) -----

def _moment(day, minute):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min) + timedelta(minutes=minute))


def _shift(shift) -> dict:
    return {"id": str(shift.pk), "starts_at": iso(_moment(shift.date, shift.start)), "ends_at": iso(_moment(shift.date, shift.end)),
            "role": shift.role.name if shift.role else ""}


def shift_item(shift) -> dict:
    """A shift as one item of a list: flat, with the keys the actions answer a shift with."""
    return {"shift_id": str(shift.pk), "starts_at": iso(_moment(shift.date, shift.start)), "ends_at": iso(_moment(shift.date, shift.end)),
            "role": shift.role.name if shift.role else ""}


@safe
def schedule_published(user_ids):
    """schedule.publish: everyone whose shifts changed."""
    user_ids = set(user_ids)

    def build():
        from django.contrib.auth.models import User

        from team.models import Shift
        today = timezone.localdate()
        out = []
        for user in User.objects.filter(pk__in=user_ids).order_by("id"):
            coming = list(Shift.objects.filter(user=user, date__gte=today, date__lt=today + timedelta(days=14), published=True, deleted=False)
                          .select_related("role").order_by("date", "start"))
            staff = _person(user)
            data = {"staff": staff, "shifts": len(coming), "upcoming_shifts": [shift_item(s) for s in coming[:LIST_LIMIT]]}
            if coming:
                data.update(next_shift_at=iso(_moment(coming[0].date, coming[0].start)), next_shift_role=coming[0].role.name if coming[0].role else "")
            out.append(({"type": "person", **staff}, data, f"{staff['name']}'s schedule changed"))
        return out
    emit("schedule.published", build)


@safe
def shift_offered(offer):
    def build():
        staff = _person(offer.by)
        return {"type": "person", **staff}, {"staff": staff, "shift": _shift(offer.shift), "note": offer.note}, f"{staff['name']} put a shift up for grabs"
    emit("shift.offered", build)


@safe
def shift_taken(shift, taker, giver=None):
    def build():
        staff = _person(taker)
        data = {"staff": staff, "shift": _shift(shift), "from": {"name": full_name(giver), "email": (giver.email or "") if giver else ""}}
        return {"type": "person", **staff}, data, f"{staff['name']} took a shift"
    emit("shift.taken", build)


@safe
def shift_swapped(offer):
    def build():
        staff = _person(offer.by)
        data = {"staff": staff, "shift": _shift(offer.shift), "kind": "swap" if offer.theirs_id else "handoff",
                "to": {"name": full_name(offer.to), "email": offer.to.email or ""}}
        return {"type": "person", **staff}, data, f"{staff['name']}'s shift went to {data['to']['name']}"
    emit("shift.swapped", build)


def _request(req):
    from team.models import TimeOff
    staff = _person(req.user)
    request = {"id": str(req.pk), "reason": req.reason}
    if isinstance(req, TimeOff):
        request.update(first_day=req.first_day.isoformat(), last_day=req.last_day.isoformat(), all_day=bool(req.all_day), category=req.category)
    else:
        request.update(effective=req.effective.isoformat(), until=req.until.isoformat() if req.until else "")
    return {"type": "person", **staff}, {"staff": staff, "request": request}, "time_off" if isinstance(req, TimeOff) else "availability"


@safe
def request_made(req):
    """requests.ask_time_off / ask_availability (their store account, or Switchboard's time_off.request)."""
    kind = _request(req)[2]

    def build():
        subject, data, _kind = _request(req)
        return subject, data, f"{subject['name']} asked for {'time off' if kind == 'time_off' else 'an availability change'}"
    emit(f"{kind}.requested", build, at=req.submitted_at)


@safe
def request_decided(req):
    """requests.decide: a manager's yes or no."""
    kind = _request(req)[2]

    def build():
        subject, data, _kind = _request(req)
        data.update(decision=req.status, message=req.message, decided_by=full_name(req.decided_by))
        return subject, data, f"{subject['name']}'s {'time off' if kind == 'time_off' else 'availability change'} was {req.status}"
    emit(f"{kind}.decided", build)


# ----- the store -----

@safe
def store_paused(until):
    """admin_portal/hours.py: pause (the Store hours page, or Switchboard's own action)."""
    emit("store.paused", lambda: (_subject("store", 1), {"until": iso(until)}, "Online orders paused"))


@safe
def store_resumed():
    at = timezone.now()
    emit("store.resumed", lambda: (_subject("store", 1), {"at": iso(at)}, "Online orders are back on"), at=at)


@safe
def store_event_created(event):
    """An events.Event was made (hooks.py): the admin's Events page, or Switchboard's own action."""
    def build():
        from events import hours
        all_day = bool(event.all_day and event.kind == event.CLOSED)
        channels = ", ".join(label for label, on in (("dine-in", event.dine_in), ("pickup", event.pickup), ("delivery", event.delivery)) if on)
        data = {"event": {"id": str(event.pk), "title": event.title, "kind": event.kind, "starts_on": event.start_date.isoformat(),
                          "ends_on": event.end_date.isoformat(), "all_day": all_day,
                          "hours": "" if all_day else hours.ranges_text([event.span()]), "channels": channels,
                          "description": event.description or ""}}
        return _subject("store_event", event.pk, event.title), data, f"{event.title} added"
    emit("store_event.created", build)


@safe
def dongle(dongle_id, online):
    """devices/socket.py: a visual dongle's connection opened (it was offline) or closed."""
    def build():
        from devices.models import Dongle
        d = Dongle.objects.select_related("screen").filter(pk=dongle_id).first()
        if d is None:
            return None
        data = {"dongle": {"id": str(d.pk), "name": d.name, "screen": d.screen.name if d.screen else ""}}
        return _subject("dongle", d.pk, d.name), data, f"{d.name} {'is online' if online else 'went offline'}"
    emit("dongle.online" if online else "dongle.offline", build)
