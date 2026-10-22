"""
Voice channels: push-to-talk relayed over a WebSocket at /ws/voice/<channel id> (routed in pavilion/asgi.py).

Anyone may listen (screens are the speakers; public ones aren't signed in). Talking needs a signed-in staff account.
A talker's page sends {"type": "start"}, then Opus packets as binary messages (20 ms each, 48 kHz mono, encoded with
WebCodecs), then {"type": "stop"}. One talker at a time per channel, like a radio: whoever holds the channel ("the
floor", kept in the cache) talks; someone else gets {"type": "busy"} until it's free.

The server passes everything on to every other connection on the channel through the realtime broker (Redis between
workers in production), and also sends {"type": "clip", "url"} for order calls (audio/calls.py). Listeners decode and
play it, ducking the channels below (screens/static/screens/audio-player.js).
"""
import json
import logging
import uuid
from http.cookies import SimpleCookie

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache

from pavilion.realtime import get_broker

logger = logging.getLogger(__name__)

FLOOR_SECONDS = 15  # a talker who goes silent without saying "stop" loses the channel after this
MAX_FRAME = 4000  # bytes: a 20 ms Opus packet is far smaller


def topic(channel_id):
    return f"voice:{channel_id}"


def floor_key(channel_id):
    return f"audio:voice-floor:{channel_id}"


def _frame(sender, kind, payload: bytes) -> bytes:
    """What goes through the broker: the sender's id (so it doesn't hear itself), T(ext) or B(inary), the payload."""
    return sender.encode() + kind + payload


def broadcast(channel_id, message: dict, sender="server______"):
    """Send a control message (e.g. a clip) to everyone on a voice channel."""
    get_broker().publish(topic(channel_id), _frame(sender, b"T", json.dumps(message).encode()))


@sync_to_async
def _who(scope, channel_id):
    """(channel exists and is voice, the staff member's name or None) from the connection's session cookie."""
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.backends.base import SessionBase
    from importlib import import_module

    from .models import AudioChannel
    if not AudioChannel.objects.filter(pk=channel_id, kind=AudioChannel.VOICE, archived=False).exists():
        return False, None
    cookies = SimpleCookie()
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            cookies.load(value.decode("latin-1"))
    morsel = cookies.get(settings.SESSION_COOKIE_NAME)
    if morsel is None:
        return True, None
    store: SessionBase = import_module(settings.SESSION_ENGINE).SessionStore(morsel.value)
    user_id = store.get("_auth_user_id")
    user = get_user_model().objects.filter(pk=user_id, is_active=True, is_staff=True).first() if user_id else None
    return True, (user.get_full_name() or user.username) if user else None


async def voice_socket(scope, receive, send, channel_id):
    """The WebSocket for one connection to a voice channel."""
    import asyncio
    event = await receive()
    if event["type"] != "websocket.connect":
        return
    exists, talker = await _who(scope, channel_id)
    if not exists:
        await send({"type": "websocket.close", "code": 4404})
        return
    await send({"type": "websocket.accept"})
    me = uuid.uuid4().hex[:12]
    broker = get_broker()
    holding = False

    async def relay():
        async for message in broker.subscribe(topic(channel_id), raw=True):
            if isinstance(message, str):
                message = message.encode("latin-1")
            sender, kind, payload = message[:12].decode(), message[12:13], message[13:]
            if sender == me:
                continue
            if kind == b"T":
                await send({"type": "websocket.send", "text": payload.decode()})
            else:
                await send({"type": "websocket.send", "bytes": payload})

    def take_floor():
        return cache.add(floor_key(channel_id), me, FLOOR_SECONDS) or cache.get(floor_key(channel_id)) == me

    def let_go():
        if cache.get(floor_key(channel_id)) == me:
            cache.delete(floor_key(channel_id))

    relaying = asyncio.ensure_future(relay())
    try:
        while True:
            event = await receive()
            if event["type"] == "websocket.disconnect":
                break
            if event.get("text") is not None:
                try:
                    message = json.loads(event["text"])
                except ValueError:
                    continue
                kind = message.get("type") if isinstance(message, dict) else None
                if kind == "start":
                    if talker is None:
                        await send({"type": "websocket.send", "text": json.dumps({"type": "denied"})})
                    elif await sync_to_async(take_floor)():
                        holding = True
                        broker.publish(topic(channel_id), _frame(me, b"T", json.dumps({"type": "start", "who": talker}).encode()))
                        await send({"type": "websocket.send", "text": json.dumps({"type": "talking"})})
                    else:
                        await send({"type": "websocket.send", "text": json.dumps({"type": "busy"})})
                elif kind == "stop" and holding:
                    holding = False
                    await sync_to_async(let_go)()
                    broker.publish(topic(channel_id), _frame(me, b"T", json.dumps({"type": "stop"}).encode()))
            elif event.get("bytes") and holding and len(event["bytes"]) <= MAX_FRAME:
                broker.publish(topic(channel_id), _frame(me, b"B", event["bytes"]))
                await sync_to_async(cache.touch)(floor_key(channel_id), FLOOR_SECONDS)
    finally:
        if holding:
            await sync_to_async(let_go)()
            broker.publish(topic(channel_id), _frame(me, b"T", json.dumps({"type": "stop"}).encode()))
        relaying.cancel()
        try:
            await relaying
        except (asyncio.CancelledError, Exception):
            pass
