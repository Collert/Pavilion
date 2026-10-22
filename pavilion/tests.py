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

    def test_one_stream_carries_several_topics(self):
        """A page listens to all its topics on one connection (browsers allow six per server over HTTP/1.1)."""
        broker = realtime.LocalBroker()

        async def run():
            with mock.patch.object(realtime, "get_broker", return_value=broker):
                stream = realtime.event_stream(["orders", "audio"])
                self.assertIn("event: ready", await stream.__anext__())
                waiting = asyncio.ensure_future(stream.__anext__())
                await asyncio.sleep(0.05)
                broker.publish("audio", "{}")
                self.assertTrue((await waiting).startswith("event: audio\n"))
                waiting = asyncio.ensure_future(stream.__anext__())
                await asyncio.sleep(0.01)
                broker.publish("menus", "{}")  # not listened to
                broker.publish("orders", "{}")
                self.assertTrue((await waiting).startswith("event: orders\n"))
                await stream.aclose()

        asyncio.run(run())
        self.assertEqual(broker._subscribers, set())

    def test_the_view_takes_a_list_and_checks_each_topic(self):
        from asgiref.sync import async_to_sync
        from django.contrib.auth.models import AnonymousUser
        from django.http import Http404
        from django.test import RequestFactory
        request = RequestFactory().get("/events/orders,checkout")
        request.user = AnonymousUser()
        self.assertEqual(async_to_sync(realtime.stream_view)(request, "orders,checkout").status_code, 403)  # checkout is private
        with self.assertRaises(Http404):
            async_to_sync(realtime.stream_view)(request, "orders,nope")
