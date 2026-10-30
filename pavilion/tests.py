"""Tests for the SSE broker (pavilion/realtime.py)."""
import asyncio
import json
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

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



@override_settings(THROTTLE=True, THROTTLE_REQUESTS=5, THROTTLE_WINDOW=10)
class ThrottleTests(TestCase):
    """pavilion/throttle.py: one client can't swamp the app."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_allow_counts_per_window(self):
        from pavilion import throttle
        results = [throttle.allow("t", "me", 3, 10, now=100.0)[0] for _ in range(4)]
        self.assertEqual(results, [True, True, True, False])
        self.assertEqual(throttle.allow("t", "me", 3, 10, now=104.2), (False, 6))  # until the window ends at 110
        self.assertTrue(throttle.allow("t", "you", 3, 10, now=104.2)[0])  # someone else
        self.assertTrue(throttle.allow("t", "me", 3, 10, now=110.0)[0])  # next window
        with self.settings(THROTTLE=False):
            self.assertTrue(all(throttle.allow("t", "me", 0, 10, now=100.0)[0] for _ in range(5)))

    @mock.patch("pavilion.throttle.time.time", return_value=1000.0)  # one window, however slow the run
    def test_middleware_limits_each_client(self, _clock):
        from django.contrib.auth.models import User
        user = User.objects.create_user("dana", is_staff=True)
        self.client.force_login(user)
        codes = [self.client.get("/apps/").status_code for _ in range(6)]
        self.assertEqual(codes[-1], 429)
        self.assertNotIn(429, codes[:5])
        response = self.client.get("/apps/", HTTP_ACCEPT="application/json")
        self.assertEqual((response.status_code, int(response["Retry-After"]) > 0), (429, True))
        self.assertIn("retryAfter", response.json())
        other = self.client_class()  # another browser isn't held up
        other.force_login(user)
        self.assertNotEqual(other.get("/apps/").status_code, 429)

    @mock.patch("pavilion.throttle.time.time", return_value=1000.0)
    def test_a_new_cookie_every_time_still_counts_against_the_address(self, _clock):
        from pavilion import throttle
        from django.test import RequestFactory
        codes = []
        for n in range(5 * throttle.ADDRESS_FACTOR + 1):
            request = RequestFactory().get("/apps/")
            request.COOKIES["sessionid"] = f"fake{n}"
            codes.append(throttle.check(request))
        self.assertTrue(all(c is None for c in codes[:-1]))
        self.assertEqual(codes[-1].status_code, 429)

    def test_live_streams_and_files_dont_count(self):
        from pavilion.throttle import SKIP, ThrottleMiddleware
        from django.http import HttpResponse
        from django.test import RequestFactory
        middleware = ThrottleMiddleware(lambda request: HttpResponse("ok"))
        for _ in range(20):
            request = RequestFactory().get("/events/orders")
            request.session = None
            self.assertEqual(middleware(request).status_code, 200)
        self.assertIn("/events/", SKIP)


class DatabaseSettingsTests(SimpleTestCase):
    """settings.py takes a DATABASE_URL connection string in place of the POSTGRES_*/DB_* parts."""
    def settings_with(self, **env):
        import os
        import runpy
        from pathlib import Path
        with mock.patch.dict(os.environ, env), mock.patch("dotenv.load_dotenv"):
            return runpy.run_path(str(Path(__file__).with_name("settings.py")))

    def test_a_connection_string(self):
        db = self.settings_with(DATABASE_URL="postgres://pavilion:p%40ss@db.example.com:6543/pavilion")["DATABASES"]["default"]
        self.assertEqual((db["ENGINE"], db["NAME"], db["USER"], db["PASSWORD"], db["HOST"], db["PORT"]),
                         ("django.db.backends.postgresql", "pavilion", "pavilion", "p@ss", "db.example.com", 6543))

    def test_the_bundled_databases_generated_password(self):
        """docker-compose.yml's db-password file wins over POSTGRES_PASSWORD, so the app and Postgres agree."""
        import os
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("Generated123\n")
        try:
            db = self.settings_with(ENVIRONMENT="production", SECRET_KEY="x" * 50, REDIS_URL="redis://r:6379/0", DATABASE_URL="",
                                    POSTGRES_PASSWORD_FILE=f.name, POSTGRES_PASSWORD="from-the-tab")["DATABASES"]["default"]
        finally:
            os.unlink(f.name)
        self.assertEqual((db["PASSWORD"], db["HOST"]), ("Generated123", "db"))


class DevCacheTests(SimpleTestCase):
    """pavilion/cache.py: Windows locks a cache file mid-write; the dev cache tries again, then gives up quietly."""
    def test_a_locked_file_is_tried_again_then_counts_as_a_miss(self):
        from pavilion.cache import _retry
        calls = []

        def flaky(self, key, default=None, version=None):
            calls.append(key)
            if len(calls) < 3:
                raise PermissionError(13, "Permission denied")
            return "value"
        get = _retry(flaky, lambda key, default=None, version=None: default)
        self.assertEqual(get(None, "k"), "value")
        self.assertEqual(len(calls), 3)

        def locked(self, key, default=None, version=None):
            raise PermissionError(13, "Permission denied")
        self.assertEqual(_retry(locked, lambda key, default=None, version=None: default)(None, "k", "miss"), "miss")

    def test_the_throttle_lets_requests_through_when_the_cache_fails(self):
        from pavilion import throttle
        with override_settings(THROTTLE=True), mock.patch.object(throttle.cache, "add", side_effect=OSError("down")):
            self.assertEqual(throttle.allow("all", "s1", 1, 10), (True, 0))


class OldAddressTests(TestCase):
    """Addresses from before the store moved under /store/ and the language prefixes went (pavilion/redirects.py)."""

    def test_store_pages_at_the_root_move_under_store(self):
        for old, new in (("/", "/store/"), ("/menu", "/store/menu"), ("/order-status/12", "/store/order-status/12"),
                         ("/menu?dish=4", "/store/menu?dish=4"), ("/catering/", "/store/catering/"), ("/apps", "/apps/")):
            response = self.client.get(old)
            self.assertEqual((response.status_code, response.url), (301, new), old)
        self.assertEqual(self.client.get("/nothing-here").status_code, 404)

    def test_a_language_prefix_picks_that_language(self):
        response = self.client.get("/uk/menu")
        self.assertEqual((response.status_code, response.url, response.cookies["django_language"].value), (301, "/store/menu", "uk"))
        self.assertEqual(self.client.get("/zh-hk/apps/").url, "/apps/")
        self.assertEqual(self.client.get("/en").url, "/store/")
        self.assertEqual(self.client.get("/en/nothing-here").status_code, 404)

    def test_the_script_translations_have_an_address_per_language(self):
        self.assertContains(self.client.get("/jsi18n/uk/"), "django")
        self.assertEqual(self.client.get("/jsi18n/xx/").status_code, 404)
