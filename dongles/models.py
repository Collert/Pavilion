from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

CODE_MINUTES = 15  # how long a pairing code works


class Dongle(models.Model):
    """
    A visual dongle: a Raspberry Pi behind a TV running jester-agent (github.com/collert/jester-agent), showing one
    Screen. It paired with a DonglePairingCode and keeps a WebSocket open to Pavilion (/ws/dongle, dongles/socket.py):
    Pavilion sends it what to show and commands (show its name, restart, change Wi-Fi), it sends back how it's doing
    (`info`). Its browser opens the screen through a launch link (dongles/views.py: open_screen), which leaves a
    signed cookie letting it see that screen without anyone signing in. Edited on the admin's Devices page.
    """
    LANDSCAPE, PORTRAIT, PORTRAIT_FLIPPED = "landscape", "portrait", "portrait_flipped"
    ORIENTATIONS = [(LANDSCAPE, _("Landscape")), (PORTRAIT, _("Portrait")), (PORTRAIT_FLIPPED, _("Portrait, flipped"))]

    name = models.CharField(_("name"), max_length=140)
    hardware_id = models.CharField(_("dongle ID"), max_length=16, help_text=_("What the TV calls it, e.g. 7F3K"))
    token_hash = models.CharField(max_length=64, help_text=_("SHA-256 of the secret part of its token"))
    screen = models.ForeignKey("screens.Screen", verbose_name=_("screen"), null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="dongles")
    orientation = models.CharField(_("turn the picture"), max_length=20, choices=ORIENTATIONS, default=LANDSCAPE)
    info = models.JSONField(default=dict, blank=True, help_text=_(
        "What it last said about itself: model, version, resolution, network {ssid, signal, ip, wired}, showing"))
    paired_at = models.DateTimeField(default=timezone.now)
    paired_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    @property
    def online(self) -> bool:
        """Its WebSocket is open (kept in the cache by dongles/socket.py, so every worker agrees)."""
        from .pairing import is_online
        return is_online(self.pk)


class DonglePairingCode(models.Model):
    """A six-digit code from the admin's Devices page ("Add visual dongle"): works once, for CODE_MINUTES."""
    code = models.CharField(max_length=6, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    dongle = models.OneToOneField(Dongle, null=True, blank=True, on_delete=models.SET_NULL, related_name="pairing_code",
                                  help_text=_("The dongle that used it"))
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = (self.created_at or timezone.now()) + timedelta(minutes=CODE_MINUTES)
        super().save(*args, **kwargs)

    @property
    def live(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()
