"""
The WebSocket a paired dongle keeps open at /ws/dongle (routed in pavilion/asgi.py). The protocol is described in
jester-agent's PROTOCOL.md; in short:

- The dongle connects with "Authorization: Bearer <token>". A token Pavilion doesn't know (the dongle was unpaired)
  is accepted and closed straight away with code 4401: the dongle forgets it and shows "Scan to start" again.
- Pavilion sends {"type": "config", ...} on connecting and whenever the dongle or its screen changes, and
  {"type": "command", "id", "command", ...} (identify, restart, reload, change_wifi) from the Devices page; both
  arrive through the broker topic "dongle:<id>" (pairing.send), so any worker can reach a dongle held by another.
- The dongle sends {"type": "status", ...} every 30 s (kept on Dongle.info and last_seen; it's online while they
  keep coming) and {"type": "ack", "id", "ok", "error"} for commands.
"""
import asyncio
import json
import logging
import uuid

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.utils import timezone

from pavilion.realtime import get_broker

from . import pairing

logger = logging.getLogger(__name__)

CLOSE_UNPAIRED = 4401
SAVE_EVERY = 60  # seconds between writes of last_seen/info while nothing changes
MAX_MESSAGE = 64 * 1024


def _bearer(scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            text = value.decode("latin-1").strip()
            if text.lower().startswith("bearer "):
                return text[7:].strip()
    return ""


@sync_to_async
def _dongle(token):
    return pairing.check_token(token)


@sync_to_async
def _config(dongle_id):
    from .models import Dongle
    dongle = Dongle.objects.select_related("screen").filter(pk=dongle_id).first()
    return pairing.config(dongle) if dongle else None


@sync_to_async
def _online(dongle_id, conn, status=None, save=False):
    """Mark the dongle online (for ONLINE_SECONDS more); with save, keep what it said and when."""
    from pavilion.realtime import publish

    from .models import Dongle
    was = cache.get(pairing.online_key(dongle_id))
    cache.set(pairing.online_key(dongle_id), conn, pairing.ONLINE_SECONDS)
    changed = False
    if save or status is not None:
        fields = {"last_seen": timezone.now()}
        if status is not None:
            before = Dongle.objects.filter(pk=dongle_id).values_list("info", flat=True).first() or {}
            quiet = ("uptime",)  # changes every time: not worth refreshing the Devices page for
            changed = {k: v for k, v in before.items() if k not in quiet} != {k: v for k, v in status.items() if k not in quiet}
            fields["info"] = status
        Dongle.objects.filter(pk=dongle_id).update(**fields)  # .update(): no post_save, so no config echo
    if was != conn or changed:
        publish("dongles")


@sync_to_async
def _offline(dongle_id, conn):
    from pavilion.realtime import publish

    from .models import Dongle
    if cache.get(pairing.online_key(dongle_id)) == conn:  # a newer connection may have taken over
        cache.delete(pairing.online_key(dongle_id))
        Dongle.objects.filter(pk=dongle_id).update(last_seen=timezone.now())
        publish("dongles")


async def dongle_socket(scope, receive, send):
    event = await receive()
    if event["type"] != "websocket.connect":
        return
    dongle = await _dongle(_bearer(scope))
    await send({"type": "websocket.accept"})
    if dongle is None:
        await send({"type": "websocket.close", "code": CLOSE_UNPAIRED})
        return
    dongle_id = dongle.pk
    conn = uuid.uuid4().hex
    out = asyncio.Lock()

    async def say(message):
        async with out:
            await send({"type": "websocket.send", "text": json.dumps(message)})

    async def relay():
        """Messages for this dongle from the rest of Pavilion."""
        async for raw in get_broker().subscribe(pairing.topic(dongle_id)):
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            kind = message.get("type")
            if kind == "config":
                config = await _config(dongle_id)
                if config is None:  # deleted meanwhile
                    await say({"type": "unpaired"})
                    return
                await say(config)
            elif kind == "command":
                await say(message)
            elif kind == "unpaired":
                await say(message)
                return

    unpaired = False

    async def relay_until_unpaired():
        nonlocal unpaired
        await relay()
        unpaired = True

    relaying = asyncio.ensure_future(relay_until_unpaired())
    await asyncio.sleep(0)  # subscribed before the first config, so nothing sent meanwhile is missed
    await _online(dongle_id, conn, save=True)
    await say(await _config(dongle_id))
    last_save = asyncio.get_running_loop().time()
    try:
        while True:
            receiving = asyncio.ensure_future(receive())
            done, _pending = await asyncio.wait({receiving, relaying}, return_when=asyncio.FIRST_COMPLETED)
            if relaying in done:
                receiving.cancel()
                # Unpaired: the dongle forgets its token. Anything else (the broker went away): it reconnects.
                await send({"type": "websocket.close", "code": CLOSE_UNPAIRED if unpaired else 1011})
                break
            event = receiving.result()
            if event["type"] == "websocket.disconnect":
                break
            text = event.get("text")
            if not text or len(text) > MAX_MESSAGE:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "status":
                now = asyncio.get_running_loop().time()
                await _online(dongle_id, conn, status=pairing.clean_info(message))
                last_save = now
            elif kind == "ack":
                if not message.get("ok"):
                    logger.warning("Dongle %s: command %s failed: %s", dongle_id, message.get("id"), message.get("error"))
            elif asyncio.get_running_loop().time() - last_save > SAVE_EVERY:
                await _online(dongle_id, conn, save=True)
                last_save = asyncio.get_running_loop().time()
    finally:
        relaying.cancel()
        try:
            await relaying
        except (asyncio.CancelledError, Exception):
            pass
        await _offline(dongle_id, conn)
