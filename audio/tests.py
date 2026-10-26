import datetime
import io
import json
import math
import struct
import tempfile
import wave
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import library, music, voice
from .models import AudioChannel, Playlist, PlaylistTrack, Track
from users.testing import crew


def tone(seconds=2.0, rate=8000):
    """A short WAV file (a sine tone)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(int(seconds * rate))))
    return buffer.getvalue()


class LibraryTests(TestCase):
    """audio/library.py: songs are kept exactly as uploaded, with the tags the browser read."""

    def test_add_track_keeps_the_file_as_is_and_the_tags_given(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        song = tone(1.0)
        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            playlist = Playlist.objects.create(name="Evenings")
            track = library.add_track(SimpleUploadedFile("Velvet Hour.wav", song, "audio/wav"),
                                      {"title": " Velvet Hour ", "artist": "Juno Hale", "year": "2024-05-01", "duration": "1.0", "bitrate": "128"},
                                      artwork=SimpleUploadedFile("cover.png", b"\x89PNG fake", "image/png"), playlist=playlist)
            with default_storage.open(track.file.name) as f:
                self.assertEqual(f.read(), song)  # not a byte changed
            self.assertEqual((track.title, track.artist, track.year, track.format, track.size, track.original_name),
                             ("Velvet Hour", "Juno Hale", 2024, "WAV", len(song), "Velvet Hour.wav"))  # a date keeps its year
            self.assertTrue(track.artwork.name.endswith(".png"))
            self.assertEqual([t.title for t in playlist.ordered_tracks()], ["Velvet Hour"])
            untitled = library.add_track(SimpleUploadedFile("track_07.wav", song, "audio/wav"), {"year": "2021"})
            self.assertEqual((untitled.title, untitled.year), ("track_07", 2021))  # no title: the file's name
            files = [track.file.name, track.artwork.name]
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(library.delete_tracks([track.id]), 1)
            self.assertFalse(any(default_storage.exists(name) for name in files))
            self.assertFalse(playlist.entries.exists())

    def test_playlist_append_and_reorder(self):
        a, b, c = (Track.objects.create(file=f"music/{n}.mp3", title=n) for n in "ABC")
        playlist = library.new_playlist([b.id, a.id])
        self.assertEqual(library.append(playlist, [a.id, c.id]), 1)  # A is already in it
        library.reorder(playlist, [c.id, b.id])
        self.assertEqual([t.title for t in playlist.ordered_tracks()], ["C", "B", "A"])


class MusicQueueTests(TestCase):
    """Music channels are radio stations playing a queue: one timeline per channel, every screen in step."""

    def setUp(self):
        self.a = Track.objects.create(file="music/a.mp3", title="A", duration=100)
        self.b = Track.objects.create(file="music/b.mp3", title="B", duration=50)
        self.c = Track.objects.create(file="music/c.mp3", title="C", duration=30)
        self.channel = AudioChannel.objects.create(name="Lobby music", kind="music")
        self.t0 = timezone.now()
        nudges = mock.patch("audio.music.publish")
        self.nudge = nudges.start()
        self.addCleanup(nudges.stop)

    def at(self, seconds):
        return self.t0 + datetime.timedelta(seconds=seconds)

    def do(self, op, seconds=0, **kw):
        return music.control(self.channel, op, now=self.at(seconds), **kw)

    def state(self, seconds):
        return music.now_playing(self.channel, self.at(seconds))

    def test_an_empty_channel_is_idle(self):
        state = self.state(0)
        self.assertEqual((state["track"], state["paused"]), (None, True))
        self.do("play")
        self.assertIsNone(self.state(5)["track"])

    def test_the_queue_plays_through_and_repeats_or_stops(self):
        self.do("playnow", ids=[self.a.id, self.b.id, self.c.id], source="Evenings")
        self.nudge.assert_called_with("audio")
        state = self.state(0)
        self.assertEqual((state["track"], state["next"], state["channel"].source), (self.a, self.b, "Evenings"))
        self.assertEqual((self.state(120)["track"], round(self.state(120)["offset"])), (self.b, 20))
        state = self.state(100 + 50 + 30 + 5 + 180 * 10)  # repeat all (the default): round and round
        self.assertEqual((state["track"], round(state["offset"])), (self.a, 5))
        self.do("repeat", 2000, value="one")
        self.assertEqual(self.state(2000 + 250)["track"], self.a)  # the song again
        self.do("repeat", 2300, value="off")
        state = self.state(2300 + 1000)  # off: it stops after the last song, back at its start
        self.assertEqual((state["track"], state["paused"], state["offset"], state["next"]), (self.c, True, 0, None))
        self.do("play", 2300 + 1000)
        self.assertFalse(self.state(2300 + 1001)["paused"])

    def test_pause_seek_skip_and_previous(self):
        self.do("playnow", ids=[self.a.id, self.b.id, self.c.id])
        self.do("pause", 10)
        state = self.state(500)
        self.assertEqual((state["track"], state["offset"], state["paused"]), (self.a, 10, True))  # time stands still
        self.do("toggle", 500)
        self.assertEqual(round(self.state(505)["offset"]), 15)  # picks up where it stopped
        self.do("seek", 505, value=80)
        self.assertEqual(round(self.state(506)["offset"]), 81)
        self.do("next", 506)
        self.assertEqual((self.state(507)["track"], round(self.state(507)["offset"])), (self.b, 1))
        self.do("prev", 510)  # 4 s into B: back to its start
        self.assertEqual((self.state(510)["track"], self.state(510)["offset"]), (self.b, 0))
        self.do("prev", 511)  # 1 s in: back to A
        self.assertEqual(self.state(511)["track"], self.a)

    def test_queue_editing(self):
        self.do("playnow", ids=[self.a.id])
        self.do("playlast", ids=[self.c.id])
        self.do("playnext", ids=[self.b.id])
        keys = lambda: [e["k"] for e in AudioChannel.objects.get(pk=self.channel.pk).queue]  # noqa: E731
        titles = lambda: [t.title for _e, t in self.state(1)["entries"]]  # noqa: E731
        self.assertEqual(titles(), ["A", "B", "C"])
        a, b, c = keys()
        self.do("tonext", key=c)
        self.assertEqual(titles(), ["A", "C", "B"])
        self.do("move", key=a, value=2)  # the playing song moves; it keeps playing
        self.assertEqual((titles(), self.state(1)["track"]), (["C", "B", "A"], self.a))
        self.do("remove", key=a)  # not the one playing
        self.assertEqual(len(keys()), 3)
        self.do("remove", key=c)
        self.assertEqual(titles(), ["B", "A"])
        self.do("jump", 2, key=b)
        self.assertEqual(self.state(3)["track"], self.b)
        self.do("clear", 3)
        self.assertEqual(titles(), ["B"])
        self.do("volume", value=140)
        self.assertEqual(AudioChannel.objects.get(pk=self.channel.pk).volume, 100)

    def test_shuffle_mixes_only_whats_up_next(self):
        many = [Track.objects.create(file=f"music/{n}.mp3", title=str(n), duration=60) for n in range(12)]
        self.do("playnow", ids=[t.id for t in many])
        self.assertEqual(self.do("shuffle"), "shuffled")
        entries = self.state(1)["entries"]
        self.assertEqual(entries[0][1], many[0])  # the playing song stays first
        self.assertEqual({t.id for _e, t in entries}, {t.id for t in many})

    def test_deleted_songs_leave_the_queue(self):
        self.do("playnow", ids=[self.a.id, self.b.id, self.c.id])
        self.do("next", 1)  # B is playing
        self.b.delete()
        state = self.state(5)
        self.assertEqual(([t.title for _e, t in state["entries"]], state["track"]), (["A", "C"], self.c))

    def test_endpoints(self):
        staff = crew(User.objects.create_user("staff", password="x", is_staff=True))
        self.do("playnow", ids=[self.a.id, self.b.id])
        response = self.client.get(reverse("audio_now", args=[self.channel.id]))
        data = response.json()
        self.assertEqual((data["track"]["title"], data["next"]["title"], data["paused"], data["volume"]), ("A", "B", False, 70))
        self.assertTrue(data["track"]["url"].endswith("music/a.mp3"))
        self.assertEqual(self.client.post(reverse("audio_control", args=[self.channel.id]), "{}", content_type="application/json").status_code, 401)
        self.client.force_login(staff)
        response = self.client.post(reverse("audio_control", args=[self.channel.id]), json.dumps({"op": "pause"}),
                                    content_type="application/json")
        self.assertTrue(response.json()["paused"])
        voice = AudioChannel.objects.create(name="Calls", kind="voice")
        self.assertEqual(self.client.get(reverse("audio_now", args=[voice.id])).status_code, 404)


class AudioAppTests(TestCase):
    """The Audio app's pages (audio/views.py, pages.py): any staff member plays music and talks."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.staff = crew(User.objects.create_user("dana", password="x", is_staff=True, first_name="Dana", last_name="Wu"))
        self.lobby = AudioChannel.objects.create(name="Lobby music", kind="music", notes="Dining room")
        self.calls = AudioChannel.objects.create(name="Order calls", kind="voice")
        self.songs = [Track.objects.create(file=f"music/{n}.mp3", title=n, artist="Juno Hale", duration=200) for n in ("Velvet Hour", "Ember")]
        self.playlist = library.new_playlist([t.id for t in self.songs])
        self.playlist.name = "Slow evenings"
        self.playlist.save()
        self.url = reverse("audio_music", args=[self.lobby.id])

    def test_staff_only(self):
        self.assertEqual(self.client.get(reverse("audio_index")).status_code, 302)
        customer = User.objects.create_user("customer", password="x")
        self.client.force_login(customer)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_playing_a_playlist_queueing_and_the_overview(self):
        self.client.force_login(self.staff)
        p = self.client.get(reverse("audio_index")).context["p"]
        self.assertEqual(([s["key"] for s in p["sections"]], p["count"]), (["music", "voice"], "1 music · 1 voice"))
        self.assertEqual(p["sections"][0]["cards"][0]["s"]["pill"], "Idle")
        response = self.client.post(self.url, {"op": f"playnow:p{self.playlist.id}", "back": self.url + "?view=browse"}, follow=True)
        self.assertContains(response, "Playing Slow evenings.")
        m = self.client.get(self.url).context["m"]
        self.assertEqual((m["track"].title, m["from"], m["playing"]), ("Velvet Hour", "Playing from Slow evenings", True))
        self.client.post(self.url, {"op": f"playnext:t{self.songs[1].id}"})
        m = self.client.get(self.url).context["m"]
        self.assertEqual([q["t"].title for q in m["queue"]], ["Velvet Hour", "Ember", "Ember"])
        self.assertEqual([q["tag"] for q in m["queue"]], ["Now playing", "Next", ""])
        self.client.post(self.url, {"op": "volume", "value": "40"})
        self.assertEqual(AudioChannel.objects.get(pk=self.lobby.id).volume, 40)
        sheet = self.client.get(self.url, {"view": "browse", "sheet": f"song:{self.songs[0].id}"}).context
        self.assertEqual(([o[0] for o in sheet["m"]["sheet"]["opts"]], sheet["back"]), (["playnow", "playnext", "playlast"], self.url + "?view=browse"))
        p = self.client.get(reverse("audio_index"), {"type": "music"}).context["p"]
        self.assertEqual((len(p["sections"]), p["sections"][0]["cards"][0]["s"]["pill"]), (1, "Playing"))
        self.assertEqual(self.client.get(reverse("audio_index"), {"q": "zzz"}).context["p"]["sections"], [])
        # a bad "back" never sends anyone off the site
        self.assertEqual(self.client.post(self.url, {"op": "toggle", "back": "https://evil.example/"}).url, self.url)

    def test_voice_page_who_is_talking_and_the_last_hour(self):
        from unittest import mock
        self.client.force_login(self.staff)
        voice.log(self.calls.id, {"kind": "call", "text": "Order for Priya S. is ready.", "url": "/media/calls/a.mp3"})
        voice.log(self.calls.id, {"kind": "voice", "name": "Marco Bellini", "first": "Marco", "initials": "MB", "len": 6})
        v = self.client.get(reverse("audio_voice", args=[self.calls.id])).context["v"]
        self.assertEqual([r["kind"] for r in v["rows"]], ["voice", "call"])
        self.assertEqual(v["last_call"]["url"], "/media/calls/a.mp3")
        from django.core.cache import cache
        cache.set(voice.who_key(self.calls.id), {"name": "Aisha Okafor", "first": "Aisha", "initials": "AO", "since": 0}, 15)
        context = self.client.get(reverse("audio_voice", args=[self.calls.id])).context
        self.assertEqual((context["v"]["rows"][0]["what"], context["v"]["status"]["head"]), ("Talking now…", "Aisha Okafor is talking"))
        self.assertTrue(context["rail"]["groups"][1]["items"][0]["live"])
        with mock.patch.object(voice, "broadcast") as sent:
            self.client.post(reverse("audio_voice", args=[self.calls.id]), {"op": "chime"})
        sent.assert_not_called()  # not over someone talking
        cache.delete(voice.who_key(self.calls.id))
        with mock.patch.object(voice, "broadcast") as sent:
            self.client.post(reverse("audio_voice", args=[self.calls.id]), {"op": "chime"})
        self.assertTrue(sent.call_args[0][1]["chime"])
        self.assertRedirects(self.client.get(reverse("audio_talk", args=[self.calls.id])), reverse("audio_voice", args=[self.calls.id]))

    def test_now_playing_widget_shows_the_channel(self):
        from django.test import RequestFactory
        from screens import providers
        request = RequestFactory().get("/")
        request.screen_audio = [{"id": "lobby", "widget": "music_channel", "params": {"channel": self.lobby.id}}]
        self.assertIsNone(providers.now_playing(request, {"channel": ""})["track"])
        music.control(self.lobby, "playnow", ids=[t.id for t in self.songs])
        data = providers.now_playing(request, {"channel": "lobby"})
        self.assertEqual((data["track"].title, data["next"].title, data["playing"], data["length"]), ("Velvet Hour", "Ember", True, "3:20"))


class VoiceRelayTests(TestCase):
    """audio/voice.py: push-to-talk over a WebSocket, one talker at a time, relayed to everyone else on the channel."""

    def test_talking_listening_busy_and_clips(self):
        import asyncio
        from django.core.cache import cache
        from pavilion import realtime
        from . import voice
        cache.clear()
        broker = realtime.LocalBroker()
        names = iter([None, voice.person(User(first_name="Dana", last_name="Wu")), voice.person(User(username="alex")), None])  # a screen, two staff, a customer

        async def who(scope, channel_id):
            return True, next(names)

        class Socket:
            def __init__(self):
                self.inbox, self.outbox = asyncio.Queue(), asyncio.Queue()

            async def receive(self):
                return await self.inbox.get()

            async def send(self, message):
                await self.outbox.put(message)

            async def next(self, kind="websocket.send"):
                while True:
                    message = await asyncio.wait_for(self.outbox.get(), 2)
                    if message["type"] == kind:
                        return message

            def say(self, **message):
                self.inbox.put_nowait({"type": "websocket.receive", **message})

        async def run():
            sockets = [Socket() for _n in range(4)]
            tasks = []
            for s in sockets:
                s.inbox.put_nowait({"type": "websocket.connect"})
                tasks.append(asyncio.ensure_future(voice.voice_socket({"headers": []}, s.receive, s.send, 7)))
                await s.next("websocket.accept")
            screen, dana, alex, guest = sockets
            await asyncio.sleep(0.05)
            dana.say(text=json.dumps({"type": "start"}))
            self.assertEqual(json.loads((await dana.next())["text"]), {"type": "talking"})
            self.assertEqual(json.loads((await screen.next())["text"]), {"type": "start", "who": "Dana Wu", "first": "Dana"})
            dana.say(bytes=b"opus-packet")
            self.assertEqual((await screen.next())["bytes"], b"opus-packet")
            alex.say(text=json.dumps({"type": "start"}))
            self.assertEqual(json.loads((await alex.next())["text"])["type"], "start")  # hears Dana start...
            alex_msgs = []
            while True:
                message = await alex.next()
                alex_msgs.append(message)
                if "text" in message and json.loads(message["text"])["type"] == "busy":
                    break
            guest.say(text=json.dumps({"type": "start"}))
            while json.loads((await guest.next()).get("text") or "{}").get("type") != "denied":
                pass
            dana.say(text=json.dumps({"type": "stop"}))
            while json.loads((await screen.next()).get("text") or "{}").get("type") != "stop":
                pass
            self.assertTrue(dana.outbox.empty() or all("bytes" not in m for m in list(dana.outbox._queue)))  # no echo
            voice.broadcast(7, {"type": "clip", "url": "/media/calls/a.mp3"})
            while True:
                message = json.loads((await screen.next()).get("text") or "{}")
                if message.get("type") == "clip":
                    self.assertEqual(message["url"], "/media/calls/a.mp3")
                    break
            for s in sockets:
                s.inbox.put_nowait({"type": "websocket.disconnect"})
            await asyncio.gather(*tasks)

        with mock.patch.object(voice, "_who", who), mock.patch.object(voice, "get_broker", return_value=broker):
            asyncio.run(run())
        self.assertEqual(broker._subscribers, set())
        self.assertIsNone(cache.get(voice.floor_key(7)))  # the channel is free again
        self.assertEqual([(e["kind"], e["first"]) for e in voice.recent(7)], [("voice", "Dana")])  # who talked, not what was said

    def test_the_route(self):
        import asyncio
        from pavilion.asgi import application
        sent = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        asyncio.run(application({"type": "websocket", "path": "/ws/nope"}, receive, send))
        self.assertEqual(sent, [{"type": "websocket.close", "code": 4404}])
        with mock.patch("audio.voice.voice_socket") as voice_socket:
            async def fake(*args):
                sent.append(args[3])
            voice_socket.side_effect = fake
            asyncio.run(application({"type": "websocket", "path": "/ws/voice/12"}, receive, send))
        self.assertEqual(sent[-1], 12)



class OrderCallTests(TestCase):
    """audio/calls.py: an order turning ready is spoken on the voice channels screens picked for it."""

    def test_ready_orders_are_called_on_the_picked_channels(self):
        from types import SimpleNamespace
        from pos_server.models import Order
        from pos_server.signals import order_ready
        from screens.models import Screen
        calls = AudioChannel.objects.create(name="Order calls", kind="voice")
        quiet = AudioChannel.objects.create(name="Kitchen", kind="voice")
        Screen.objects.create(name="Board", slug="board-calls", layout={"widget": "clock"},
                              audio=[{"id": "c1", "widget": "voice_channel", "params": {"channel": calls.id}},
                                     {"id": "c2", "widget": "voice_channel", "params": {"channel": quiet.id}}],
                              popups=[{"widget": "order_ready", "params": {"channel": "c1"}},
                                      {"widget": "announcement", "params": {"channel": "c2"}}])
        Screen.objects.create(name="Silent", slug="silent", layout={"widget": "clock"},
                              audio=[{"id": "c1", "widget": "voice_channel", "params": {"channel": quiet.id}}],
                              popups=[{"widget": "order_ready", "params": {"channel": "c1", "speak": False}}])
        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media),                 mock.patch("misc_tools.speech.speech_mp3", return_value=b"ID3mp3") as speech,                 mock.patch("audio.voice.broadcast") as broadcast:
            order_ready.send(sender=Order, order=SimpleNamespace(channel="web", name="Priya Singh", id=142))
            speech.assert_called_once_with("Order for Priya S. is ready.")
            self.assertEqual([c.args[0] for c in broadcast.call_args_list], [calls.id])  # not the silent screen's
            clip = broadcast.call_args.args[1]
            self.assertEqual(clip["type"], "clip")
            self.assertTrue(default_storage.exists(clip["url"].split("/media/", 1)[1]))
            from .calls import speak
            speak("Closing in ten minutes", "announcement")
            self.assertEqual(broadcast.call_args.args[0], quiet.id)

    def test_nothing_is_made_when_no_screen_wants_it(self):
        from .calls import speak
        with mock.patch("misc_tools.speech.speech_mp3") as speech:
            speak("Hello", "order_ready")
        speech.assert_not_called()
