"""Tests for the SSE broker (pavilion/realtime.py)."""
import asyncio
import json
from unittest import mock

from django.test import SimpleTestCase, TestCase

from pavilion import realtime


async def first_message(broker, topic, publish):
    """Subscribe, publish once the subscription is live, and return the first message received."""
    stream = broker.subscribe(topic).__aiter__()
    receive = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0.05)
    publish()
    try:
        return await asyncio.wait_for(receive, timeout=2)
    finally:
        await stream.aclose()


class LocalBrokerTests(SimpleTestCase):
    def test_delivers_to_subscribers_of_the_topic(self):
        broker = realtime.LocalBroker()
        message = asyncio.run(first_message(broker, "orders", lambda: (broker.publish("inventory", "x"), broker.publish("orders", "hello"))))
        self.assertEqual(message, "hello")


class RedisBrokerTests(SimpleTestCase):
    def test_round_trip_through_redis(self):
        try:
            import fakeredis
        except ImportError:
            self.skipTest("fakeredis not installed (pip install -r requirements-dev.txt)")
        server = fakeredis.FakeServer()
        with mock.patch("redis.Redis.from_url", lambda url: fakeredis.FakeRedis(server=server)), \
             mock.patch("redis.asyncio.Redis.from_url", lambda url: fakeredis.FakeAsyncRedis(server=server)):
            broker = realtime.RedisBroker("redis://fake")
            message = asyncio.run(first_message(broker, "orders", lambda: broker.publish("orders", "hi")))
        self.assertEqual(message, "hi")


class PublishTests(TestCase):
    def test_one_event_per_transaction(self):
        broker = mock.Mock()
        with mock.patch.object(realtime, "get_broker", return_value=broker):
            with self.captureOnCommitCallbacks(execute=True):
                realtime.publish("orders")
                realtime.publish("orders")
                realtime.publish("inventory")
        topics = [call.args[0] for call in broker.publish.call_args_list]
        self.assertEqual(sorted(topics), ["inventory", "orders"])
        self.assertEqual(json.loads(broker.publish.call_args_list[0].args[1])["topic"], topics[0])

    def test_events_say_which_tab_caused_them(self):
        """A screen skips the echo of its own POST: the request's X-Live-Origin travels with the event."""
        from django.test import RequestFactory
        broker = mock.Mock()

        def view(request):
            realtime.publish("cookery")
            return None

        with mock.patch.object(realtime, "get_broker", return_value=broker):
            with self.captureOnCommitCallbacks(execute=True):
                realtime.LiveOriginMiddleware(view)(RequestFactory().post("/", HTTP_X_LIVE_ORIGIN="tab-1"))
            with self.captureOnCommitCallbacks(execute=True):
                realtime.publish("orders")  # outside any request: no origin (another topic: one event per topic per transaction)
        messages = [json.loads(call.args[1]) for call in broker.publish.call_args_list]
        self.assertEqual([m.get("origin") for m in messages], ["tab-1", None])


class EventStreamTests(SimpleTestCase):
    def test_client_disconnecting_while_the_stream_waits(self):
        """A dropped connection cancels the stream mid-wait; cleanup must not fail and must unsubscribe."""
        broker = realtime.LocalBroker()

        async def run():
            with mock.patch.object(realtime, "get_broker", return_value=broker):
                stream = realtime.event_stream("orders")
                self.assertIn("event: ready", await stream.__anext__())
                waiting = asyncio.ensure_future(stream.__anext__())
                await asyncio.sleep(0.05)
                waiting.cancel()  # what the ASGI handler does when the browser goes away
                try:
                    await waiting
                except asyncio.CancelledError:
                    pass
                await stream.aclose()

        asyncio.run(run())
        self.assertEqual(broker._subscribers, set())
