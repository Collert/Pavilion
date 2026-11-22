"""
Sending Pavilion's events to every connected Switchboard (protocol §4).

    events.order_placed(order)        # pos_server.orders.create_order: every channel
    events.order_ready(order)         # pos_server.signals.order_ready: the last station finished it
    events.clocked_in(entry)          # team/clock.py
    events.clocked_out(entry, now)

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
import time
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.utils import timezone

from payments import secrets

from .connections import BUILDING
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
    """Send `event_type` once the current transaction commits. `build()` returns (subject, data, summary) and is only
    called when a Switchboard is connected. Never raises: an event must not break what caused it."""
    try:
        at = at or timezone.now()
        transaction.on_commit(lambda: _committed(event_type, build, at), robust=True)
    except Exception:
        logger.exception("Couldn't queue the Switchboard event %s", event_type)


def _committed(event_type, build, at):
    try:
        connections = list(SwitchboardConnection.objects.filter(broken_at__isnull=True))
        if not connections:
            return
        subject, data, summary = build()
        event_id = "evt_" + tokens.token_hex(10)
        ids = []
        for connection in connections:
            envelope = {"id": event_id, "type": event_type,
                        "source": {"building": BUILDING, "instance": connection.base_url},
                        "occurred_at": timezone.localtime(at).isoformat(timespec="seconds"), "subject": subject, "data": data}
            ids.append(Delivery.objects.create(connection=connection, event_id=event_id, event_type=event_type, summary=summary[:140],
                                               body=json.dumps(envelope, ensure_ascii=False), created_at=at, next_attempt_at=at).pk)
        background(lambda: _send(ids))
    except Exception:
        logger.exception("Couldn't send the Switchboard event %s", event_type)


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

def _order(order):
    customer = order.customer
    email = order.email or (customer.email if customer else "")
    name = order.name or (customer.get_full_name() if customer else "") or ""
    kind = {"web": "pickup", "delivery": "delivery"}.get(order.channel) or ("to_go" if order.to_go_order else "dine_in")
    number = f"{order.pk:04d}"
    items = sum(order.items.filter(parent__isnull=True).values_list("quantity", flat=True))
    subject = {"type": "order", "id": str(order.pk), "email": email, "name": name}
    data = {"order": {"id": str(order.pk), "number": number, "type": kind, "channel": order.channel, "total": float(order.total), "items": int(items)},
            "customer": {"name": name, "email": email}}
    return subject, data, number


def order_placed(order):
    def build():
        subject, data, number = _order(order)
        return subject, data, f"Order {number} placed"
    emit("order.placed", build, at=order.timestamp)


def order_ready(order):
    def build():
        subject, data, number = _order(order)
        return subject, data, f"Order {number} is ready"
    emit("order.ready", build)


def _staff(entry, at):
    user = entry.user
    name = user.get_full_name() or user.username
    role = entry.shift.role.name if entry.shift and entry.shift.role else ""
    staff = {"id": str(user.pk), "name": name, "email": user.email}
    return {"type": "person", **staff}, {"staff": staff, "at": timezone.localtime(at).isoformat(timespec="seconds"), "role": role}, name


def clocked_in(entry):
    def build():
        subject, data, name = _staff(entry, entry.clock_in)
        return subject, data, f"{name} clocked in"
    emit("staff.clocked_in", build, at=entry.clock_in)


def clocked_out(entry, now=None):
    at = entry.clock_out or now or timezone.now()

    def build():
        subject, data, name = _staff(entry, at)
        data["hours"] = round(entry.worked_seconds(at) / 3600, 2)
        return subject, data, f"{name} clocked out"
    emit("staff.clocked_out", build, at=at)
