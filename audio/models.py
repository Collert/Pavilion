"""
Audio: the music library and the channels screens play (design: AdminAudio, AdminLibrary).

A **music** channel is a radio station: its timeline (which song, when it started) lives here, every screen on it plays
that song from the media storage at the same moment (audio/music.py), and changes nudge screens over SSE ("audio").
A **voice** channel is push-to-talk: audio is relayed live over a WebSocket (audio/voice.py), and order calls are
spoken on it.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _


class Track(models.Model):
    """A song in the library: a file under files/music/ in the media storage, kept as it was uploaded, with what its
    tags said (read by the browser before uploading: audio/library.py) and anything the admin changed since."""
    file = models.FileField(upload_to="files/music/", max_length=300, unique=True)
    title = models.CharField(max_length=200)
    artist = models.CharField(max_length=200, blank=True)
    album = models.CharField(max_length=200, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    genre = models.CharField(max_length=80, blank=True)
    duration = models.FloatField(default=0, help_text="Seconds")
    artwork = models.ImageField(upload_to="files/music/artwork/", blank=True, max_length=300)
    format = models.CharField(max_length=10, blank=True, help_text="MP3, M4A, FLAC...")
    bitrate = models.PositiveIntegerField(default=0, help_text="kbps")
    size = models.PositiveBigIntegerField(default=0, help_text="Bytes")
    original_name = models.CharField(max_length=255, blank=True, help_text="The file's name when it was uploaded")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title", "id"]

    def __str__(self):
        return f"{self.title} · {self.artist}" if self.artist else self.title


class Playlist(models.Model):
    name = models.CharField(max_length=120, blank=True, help_text="Blank: Untitled playlist")
    description = models.CharField(max_length=200, blank=True)
    tracks = models.ManyToManyField(Track, through="PlaylistTrack", related_name="playlists", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def ordered_tracks(self):
        return [entry.track for entry in self.entries.select_related("track").order_by("position", "id")]


class PlaylistTrack(models.Model):
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="entries")
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="+")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class AudioChannel(models.Model):
    """A music or voice channel, shared by any screens that play it (each screen's Audio tab picks channels)."""
    MUSIC, VOICE = "music", "voice"
    KINDS = [(MUSIC, _("Music")), (VOICE, _("Voice"))]

    name = models.CharField(max_length=60, unique=True)
    kind = models.CharField(max_length=5, choices=KINDS, default=MUSIC)
    notes = models.CharField(max_length=200, blank=True)
    archived = models.BooleanField(default=False)
    # music: what plays, and the timeline (audio/music.py)
    playlist = models.ForeignKey(Playlist, null=True, blank=True, on_delete=models.SET_NULL, related_name="channels",
                                 help_text="Empty: the whole library")
    shuffle = models.BooleanField(default=False)
    order = models.JSONField(default=list, blank=True, help_text="Track ids in play order (the playlist, shuffled or not)")
    index = models.PositiveIntegerField(default=0, help_text="Which of `order` is playing")
    started_at = models.DateTimeField(null=True, blank=True, help_text="When the current song started (moved back by any pause)")
    paused_at = models.DateTimeField(null=True, blank=True, help_text="Set while paused")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
