"""
Server-sent events (SSE) for the screens.

How it works:
- Code that changes something a screen shows calls `publish(topic)` (usually from a model signal, after the
  transaction commits). Topics: "orders", "inventory", "checkout", "cookery", "screens" (a Screen was edited).
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

TOPICS = ("orders", "inventory", "checkout", "cookery", "screens", "menus", "catering", "popups", "audio")
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

    async def subscribe(self, topic, raw=False):
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

    async def subscribe(self, topic, raw=False):
        """Messages on `topic`, as text (raw=True: as bytes, for voice packets)."""
        import redis.asyncio
        client = redis.asyncio.Redis.from_url(self._url)
        pubsub = client.pubsub()
        await pubsub.subscribe(CHANNEL_PREFIX + topic)
        try:
            async for item in pubsub.listen():
                if item.get("type") == "message":
                    data = item["data"]
                    yield data if raw else data.decode() if isinstance(data, bytes) else data
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

async def event_stream(topics):
    """SSE body for one or more topics: a 'ready' event, then one event per published message (named after its
    topic), with a 'ping' event as a heartbeat. A page listens to all its topics on one stream: browsers allow only
    six connections to a server over HTTP/1.1, and one stream per topic used them all up (pages and files hung)."""
    if isinstance(topics, str):
        topics = [topics]
    yield "retry: 3000\nevent: ready\ndata: {}\n\n"
    readers = {topic: get_broker().subscribe(topic).__aiter__() for topic in topics}
    pending = {asyncio.ensure_future(reader.__anext__()): topic for topic, reader in readers.items()}
    try:
        while True:
            done, _ = await asyncio.wait(pending.keys(), timeout=HEARTBEAT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                yield "event: ping\ndata: {}\n\n"  # an event, not a comment, so clients can tell the stream is alive
                continue
            for future in done:
                topic = pending.pop(future)
                yield f"event: {topic}\ndata: {future.result()}\n\n"
                pending[asyncio.ensure_future(readers[topic].__anext__())] = topic
    finally:
        # The browser went away. Stop the pending reads and let them actually finish before closing the
        # subscriptions: cancel() only requests it, and aclose() on a generator that is still mid-await raises
        # "asynchronous generator is already running".
        for future in pending:
            future.cancel()
        for future in pending:
            try:
                await future
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
        for reader in readers.values():
            await reader.aclose()


def stream_response(topics):
    response = StreamingHttpResponse(event_stream(topics), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # nginx: don't buffer the stream
    return response


@sync_to_async
def _can_listen(request, topic):
    from pos_server.devices import is_registered_device
    if topic in ("orders", "screens", "catering", "menus", "inventory", "popups", "audio"):
        return True  # public screens (menu boards, pop-ups, music) and customers' catering pages listen too; a nudge carries no data, the page they refetch decides what to show
    user = request.user
    if not user.is_authenticated:
        return False
    if topic == "checkout":
        return is_registered_device(request)
    return user.is_staff


async def stream_view(request, topic):
    """GET /events/<topic>[,<topic>...]: one SSE stream of change notifications for every topic a page listens to."""
    topics = list(dict.fromkeys(t for t in topic.split(",") if t))
    if not topics or any(t not in TOPICS for t in topics):
        raise Http404
    for t in topics:
        if not await _can_listen(request, t):
            return HttpResponseForbidden()
    return stream_response(topics)
