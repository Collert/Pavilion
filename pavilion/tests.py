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
