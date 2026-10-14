"""
Server-sent events (SSE) for the screens.

How it works:
- Code that changes something a screen shows calls `publish(topic)` (usually from a model signal, after the
  transaction commits). Topics: "orders", "inventory", "checkout".
- Screens open an EventSource on `/events/<topic>` (see `stream_view`). Each event is a small JSON nudge;
  the screen then refetches its normal JSON endpoint (e.g. active_orders). The JSON endpoints stay the single
  source of truth, including what anonymous screens are allowed to see.
- Screens don't poll. They refetch whenever the stream (re)connects, and reconnect if the heartbeat stops
  (static/js/live.js), so a dropped stream only delays updates.

Why Redis: production runs several worker processes. An event published in one process has to reach streams
held open by the others, which an in-memory list can't do (that's what broke the original SSE in 2024).
With REDIS_URL set, events go through Redis pub/sub. Without it (local dev, one process) an in-process
broker is used.

Streams are async generators, so under ASGI (daphne in dev via `runserver`, uvicorn in production) an open
stream doesn't tie up a worker thread.
"""
import asyncio
import json
import logging
import threading
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponseForbidden, StreamingHttpResponse

logger = logging.getLogger(__name__)

TOPICS = ("orders", "inventory", "checkout", "cookery")
HEARTBEAT_SECONDS = 15
CHANNEL_PREFIX = "pavilion:"


# ---------- brokers ----------

class LocalBroker:
    """Single-process pub/sub for development. Thread-safe: publishers may run in any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers = set()  # (loop, asyncio.Queue, topic)

    def publish(self, topic, message):
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue, wanted in subscribers:
            if wanted == topic:
                loop.call_soon_threadsafe(queue.put_nowait, message)

    async def subscribe(self, topic):
        queue = asyncio.Queue()
        entry = (asyncio.get_running_loop(), queue, topic)
        with self._lock:
            self._subscribers.add(entry)
        try:
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                self._subscribers.discard(entry)


class RedisBroker:
    def __init__(self, url):
        import redis
        import redis.asyncio
        self._url = url
        self._sync = redis.Redis.from_url(url)

    def publish(self, topic, message):
        self._sync.publish(CHANNEL_PREFIX + topic, message)

    async def subscribe(self, topic):
        import redis.asyncio
        client = redis.asyncio.Redis.from_url(self._url)
        pubsub = client.pubsub()
        await pubsub.subscribe(CHANNEL_PREFIX + topic)
        try:
            async for item in pubsub.listen():
                if item.get("type") == "message":
                    data = item["data"]
                    yield data.decode() if isinstance(data, bytes) else data
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            await client.aclose()


_broker = None


def get_broker():
    global _broker
    if _broker is None:
        _broker = RedisBroker(settings.REDIS_URL) if settings.REDIS_URL else LocalBroker()
    return _broker


# ---------- publishing ----------

def publish(topic, **data):
    """
    Tell every screen listening on `topic` that something changed. Sent after the current DB transaction
    commits, so a screen that refetches sees the new data. Never raises: a missed nudge is caught by the
    screens' fallback poll.
    """
    assert topic in TOPICS, topic
    connection = transaction.get_connection()
    if connection.in_atomic_block and any(getattr(entry[1], "realtime_topic", None) == topic for entry in connection.run_on_commit):
        return  # already queued for this transaction; one nudge is enough

    def send():
        message = json.dumps({"topic": topic, "at": time.time(), **data})
        try:
            get_broker().publish(topic, message)
        except Exception:
            logger.exception("Publishing %s event failed", topic)

    send.realtime_topic = topic
    transaction.on_commit(send)


# ---------- streaming ----------

async def event_stream(topic):
    """SSE body: a 'ready' event, then one event per published message, with a 'ping' event as a heartbeat."""
    yield f"retry: 3000\nevent: ready\ndata: {{}}\n\n"
    messages = get_broker().subscribe(topic).__aiter__()
    next_message = asyncio.ensure_future(messages.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({next_message}, timeout=HEARTBEAT_SECONDS)
            if not done:
                yield "event: ping\ndata: {}\n\n"  # an event, not a comment, so clients can tell the stream is alive
                continue
            message = next_message.result()
            yield f"event: {topic}\ndata: {message}\n\n"
            next_message = asyncio.ensure_future(messages.__anext__())
    finally:
        # The browser went away. Stop the pending read and let it actually finish before closing the
        # subscription: cancel() only requests it, and aclose() on a generator that is still mid-await raises
        # "asynchronous generator is already running".
        next_message.cancel()
        try:
            await next_message
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        await messages.aclose()


def stream_response(topic):
    response = StreamingHttpResponse(event_stream(topic), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # nginx: don't buffer the stream
    return response


@sync_to_async
def _can_listen(request, topic):
    from pos_server.devices import is_registered_device
    if topic == "orders":
        return True  # the public status board listens too; the nudge itself carries no order data
    user = request.user
    if not user.is_authenticated:
        return False
    if topic == "checkout":
        return is_registered_device(request)
    return user.is_staff


async def stream_view(request, topic):
    """GET /events/<topic>: an SSE stream of change notifications."""
    if topic not in TOPICS:
        raise Http404
    if not await _can_listen(request, topic):
        return HttpResponseForbidden()
    return stream_response(topic)
