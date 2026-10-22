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
