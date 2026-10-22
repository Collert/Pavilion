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

from . import music
from .library import scan
from .models import AudioChannel, Playlist, PlaylistTrack, Track


def tone(seconds=2.0, rate=8000):
    """A short WAV file (a sine tone)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(int(seconds * rate))))
    return buffer.getvalue()


class LibraryScanTests(TestCase):
    def test_scan_adds_new_files_and_drops_missing_ones(self):
        from django.core.files.base import ContentFile
        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            default_storage.save("files/music/Velvet_Hour.wav", ContentFile(tone(2.0)))
            default_storage.save("files/music/evenings/Ember.wav", ContentFile(tone(1.0)))
            default_storage.save("files/music/notes.txt", ContentFile(b"not a song"))
            self.assertEqual(scan(), {"added": 2, "removed": 0, "total": 2})
            velvet = Track.objects.get(title="Velvet Hour")  # the file name, when there are no tags
            self.assertAlmostEqual(velvet.duration, 2.0, places=1)
            self.assertEqual(velvet.format, "WAV")
            self.assertEqual(scan()["added"], 0)  # already known
            default_storage.delete("files/music/evenings/Ember.wav")
            self.assertEqual(scan(), {"added": 0, "removed": 1, "total": 1})


class MusicTimelineTests(TestCase):
    """Music channels are radio stations: one timeline per channel, every screen in step."""

    def setUp(self):
        self.a = Track.objects.create(file="files/music/a.mp3", title="A", duration=100)
        self.b = Track.objects.create(file="files/music/b.mp3", title="B", duration=50)
        self.c = Track.objects.create(file="files/music/c.mp3", title="C", duration=30)
        self.channel = AudioChannel.objects.create(name="Lobby music", kind="music")
        self.t0 = timezone.now()
        cache_patch = mock.patch("audio.music._maybe_scan")
        cache_patch.start()
        self.addCleanup(cache_patch.stop)

    def at(self, seconds):
        return self.t0 + datetime.timedelta(seconds=seconds)

    def test_the_timeline_moves_with_time_and_loops(self):
        state = music.now_playing(self.channel, self.at(0))  # starts now, the whole library in title order
        self.assertEqual((state["track"], state["offset"], state["next"]), (self.a, 0, self.b))
        state = music.now_playing(self.channel, self.at(120))
        self.assertEqual((state["track"], state["offset"]), (self.b, 20))
        state = music.now_playing(self.channel, self.at(100 + 50 + 30 + 5))  # past the end: round again
        self.assertEqual((state["track"], round(state["offset"])), (self.a, 5))

    def test_pause_play_next_previous_and_playlists(self):
        music.now_playing(self.channel, self.at(0))
        with mock.patch("django.utils.timezone.now", return_value=self.at(10)), mock.patch("audio.music.publish") as nudge:
            music.control(self.channel, "pause")
        nudge.assert_called_with("audio")
        state = music.now_playing(self.channel, self.at(500))
        self.assertEqual((state["track"], state["offset"], state["paused"]), (self.a, 10, True))  # time stands still
        with mock.patch("django.utils.timezone.now", return_value=self.at(500)), mock.patch("audio.music.publish"):
            music.control(self.channel, "play")
        self.assertEqual(round(music.now_playing(self.channel, self.at(505))["offset"]), 15)  # picks up where it stopped
        with mock.patch("django.utils.timezone.now", return_value=self.at(505)), mock.patch("audio.music.publish"):
            music.control(self.channel, "next")
        self.assertEqual(music.now_playing(self.channel, self.at(506))["track"], self.b)
        with mock.patch("django.utils.timezone.now", return_value=self.at(506)), mock.patch("audio.music.publish"):
            music.control(self.channel, "previous")
            music.control(self.channel, "previous")
        self.assertEqual(music.now_playing(self.channel, self.at(507))["track"], self.c)  # wraps round
        playlist = Playlist.objects.create(name="Evenings")
        PlaylistTrack.objects.create(playlist=playlist, track=self.c, position=0)
        PlaylistTrack.objects.create(playlist=playlist, track=self.a, position=1)
        with mock.patch("django.utils.timezone.now", return_value=self.at(600)), mock.patch("audio.music.publish"):
            music.control(self.channel, "playlist", playlist=playlist)
        state = music.now_playing(self.channel, self.at(640))
        self.assertEqual((state["track"], round(state["offset"])), (self.a, 10))  # C (30 s), then A

    def test_endpoints(self):
        staff = User.objects.create_user("staff", password="x", is_staff=True)
        response = self.client.get(reverse("audio_now", args=[self.channel.id]))
        data = response.json()
        self.assertEqual((data["track"]["title"], data["next"]["title"], data["paused"]), ("A", "B", False))
        self.assertTrue(data["track"]["url"].endswith("files/music/a.mp3"))
        self.assertEqual(self.client.post(reverse("audio_control", args=[self.channel.id]), "{}", content_type="application/json").status_code, 401)
        self.client.force_login(staff)
        response = self.client.post(reverse("audio_control", args=[self.channel.id]), json.dumps({"action": "pause"}),
                                    content_type="application/json")
        self.assertTrue(response.json()["paused"])
        voice = AudioChannel.objects.create(name="Calls", kind="voice")
        self.assertEqual(self.client.get(reverse("audio_now", args=[voice.id])).status_code, 404)



class VoiceRelayTests(TestCase):
    """audio/voice.py: push-to-talk over a WebSocket, one talker at a time, relayed to everyone else on the channel."""

    def test_talking_listening_busy_and_clips(self):
        import asyncio
        from django.core.cache import cache
        from pavilion import realtime
        from . import voice
        cache.clear()
        broker = realtime.LocalBroker()
        names = iter([None, "Dana", "Alex", None])  # a screen listening, two staff, a customer who tries to talk

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
            self.assertEqual(json.loads((await screen.next())["text"]), {"type": "start", "who": "Dana"})
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
            voice.broadcast(7, {"type": "clip", "url": "/media/files/calls/a.mp3"})
            while True:
                message = json.loads((await screen.next()).get("text") or "{}")
                if message.get("type") == "clip":
                    self.assertEqual(message["url"], "/media/files/calls/a.mp3")
                    break
            for s in sockets:
                s.inbox.put_nowait({"type": "websocket.disconnect"})
            await asyncio.gather(*tasks)

        with mock.patch.object(voice, "_who", who), mock.patch.object(voice, "get_broker", return_value=broker):
            asyncio.run(run())
        self.assertEqual(broker._subscribers, set())
        self.assertIsNone(cache.get(voice.floor_key(7)))  # the channel is free again

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
