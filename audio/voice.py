"""
Voice channels: push-to-talk relayed over a WebSocket at /ws/voice/<channel id> (routed in pavilion/asgi.py).

Anyone may listen (screens are the speakers; public ones aren't signed in). Talking needs a signed-in staff account.
A talker's page sends {"type": "start"}, then Opus packets as binary messages (20 ms each, 48 kHz mono, encoded with
WebCodecs), then {"type": "stop"}. One talker at a time per channel, like a radio: whoever holds the channel ("the
floor", kept in the cache) talks; someone else gets {"type": "busy"} until it's free.

The server passes everything on to every other connection on the channel through the realtime broker (Redis between
workers in production), and also sends {"type": "clip", "url"} for order calls (audio/calls.py) and chimes. Listeners
decode and play it, ducking the channels below (screens/static/screens/audio-player.js, audio/static/audio/voice.js).

Who's talking (talking()) and the last hour's messages (recent(): who talked and for how long, and the order calls,
which can be played again) are kept in the cache for the Audio app; each change nudges "audio". Voices aren't
recorded.
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
LOG_SECONDS = 3600  # recent messages are kept for an hour
LOG_SIZE = 40


def topic(channel_id):
    return f"voice:{channel_id}"


def floor_key(channel_id):
    return f"audio:voice-floor:{channel_id}"


def who_key(channel_id):
    return f"audio:voice-who:{channel_id}"


def log_key(channel_id):
    return f"audio:voice-log:{channel_id}"


def talking(channel_id):
    """{"name", "first", "initials", "since" (epoch seconds)} of whoever is talking on the channel, or None."""
    return cache.get(who_key(channel_id))


def recent(channel_id) -> list:
    """The last hour on the channel, newest first: {"kind": "voice", "name", "first", "initials", "at", "len"} for
    someone talking, {"kind": "call", "text", "url", "at"} for an order call or announcement."""
    import time
    cutoff = time.time() - LOG_SECONDS
    return [e for e in cache.get(log_key(channel_id)) or [] if e.get("at", 0) >= cutoff]


def log(channel_id, entry):
    import time
    cache.set(log_key(channel_id), ([{"at": time.time(), **entry}] + recent(channel_id))[:LOG_SIZE], LOG_SECONDS)


def person(user) -> dict:
    name = user.get_full_name() or user.username
    parts = [p for p in name.split() if p]
    return {"name": name, "first": parts[0] if parts else name, "initials": "".join(p[0] for p in parts[:2]).upper() or name[:2].upper()}


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
    return True, person(user) if user else None


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
        import time
        from pavilion.realtime import publish
        if not (cache.add(floor_key(channel_id), me, FLOOR_SECONDS) or cache.get(floor_key(channel_id)) == me):
            return False
        cache.set(who_key(channel_id), {**talker, "since": time.time()}, FLOOR_SECONDS)
        publish("audio")
        return True

    def let_go():
        import time
        from pavilion.realtime import publish
        if cache.get(floor_key(channel_id)) == me:
            who = cache.get(who_key(channel_id)) or {**talker, "since": time.time()}
            cache.delete_many([floor_key(channel_id), who_key(channel_id)])
            log(channel_id, {"kind": "voice", **{k: who[k] for k in ("name", "first", "initials")},
                             "len": max(1, round(time.time() - who["since"]))})
            publish("audio")

    def keep_floor():
        cache.touch(floor_key(channel_id), FLOOR_SECONDS)
        cache.touch(who_key(channel_id), FLOOR_SECONDS)

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
                        broker.publish(topic(channel_id), _frame(me, b"T", json.dumps({"type": "start", "who": talker["name"], "first": talker["first"]}).encode()))
                        await send({"type": "websocket.send", "text": json.dumps({"type": "talking"})})
                    else:
                        await send({"type": "websocket.send", "text": json.dumps({"type": "busy"})})
                elif kind == "stop" and holding:
                    holding = False
                    await sync_to_async(let_go)()
                    broker.publish(topic(channel_id), _frame(me, b"T", json.dumps({"type": "stop"}).encode()))
            elif event.get("bytes") and holding and len(event["bytes"]) <= MAX_FRAME:
                broker.publish(topic(channel_id), _frame(me, b"B", event["bytes"]))
                await sync_to_async(keep_floor)()
    finally:
        if holding:
            await sync_to_async(let_go)()
            broker.publish(topic(channel_id), _frame(me, b"T", json.dumps({"type": "stop"}).encode()))
        relaying.cancel()
        try:
            await relaying
        except (asyncio.CancelledError, Exception):
            pass
