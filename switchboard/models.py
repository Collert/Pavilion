"""
Switchboard connections (the contract is Switchboard's docs/PROTOCOL.md; this app is Pavilion's side of it).

- SwitchboardConnection: one Switchboard that connected to this Pavilion. Its API key is kept only as a SHA-256 hash;
  its webhook secret is encrypted (payments/secrets.py), because Pavilion signs every event with it.
- AuthorizeCode: the one-time code the consent page hands back to Switchboard, traded once for a key (10 minutes).
- IdempotencyRecord: (connection, Idempotency-Key) → the answer given, so a repeated call isn't done twice.
- Delivery: the log of events sent to a connection (type, time, status, attempts), kept about two weeks. The signed
  body is only kept while the delivery is still being retried.
- IssuedCard: which staff cards a connection issued, so "replace previous" knows which card to retire.
"""
import hashlib
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.utils import timezone

CODE_MINUTES = 10  # how long an authorize code works
IDEMPOTENCY_DAYS = 30  # answers are replayed for at least 7 days; kept a month
LOG_DAYS = 14  # the delivery log


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


class SwitchboardConnection(models.Model):
    name = models.CharField(max_length=120, help_text="The organization's name in that Switchboard")
    webhook_url = models.CharField(max_length=1000, help_text="Where this Pavilion's events are posted")
    base_url = models.CharField(max_length=300, blank=True, help_text="This Pavilion's address as that Switchboard knows it")
    key_hash = models.CharField(max_length=64, unique=True, help_text="SHA-256 of the API key (the key itself is never kept)")
    secret = models.TextField(help_text="The webhook secret, encrypted (payments/secrets.py)")
    connected_at = models.DateTimeField(default=timezone.now)
    connected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_used_at = models.DateTimeField(null=True, blank=True, help_text="The last API call with its key")
    last_delivery_at = models.DateTimeField(null=True, blank=True, help_text="The last event Switchboard took")
    broken_at = models.DateTimeField(null=True, blank=True, help_text="Switchboard answered 410: it no longer knows this connection")

    class Meta:
        ordering = ["connected_at", "id"]

    def __str__(self):
        return f"{self.name} ({self.host})"

    @property
    def host(self) -> str:
        return urlsplit(self.webhook_url).netloc

    @property
    def broken(self) -> bool:
        return self.broken_at is not None


class AuthorizeCode(models.Model):
    code_hash = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120, blank=True)
    host = models.CharField(max_length=255, blank=True, help_text="The host of the callback the admin approved")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = (self.created_at or timezone.now()) + timedelta(minutes=CODE_MINUTES)
        super().save(*args, **kwargs)


class IdempotencyRecord(models.Model):
    connection = models.ForeignKey(SwitchboardConnection, on_delete=models.CASCADE, related_name="idempotency")
    key = models.CharField(max_length=200)
    action = models.CharField(max_length=60)
    status = models.PositiveSmallIntegerField(default=0, help_text="The HTTP status answered; 0 while the action runs")
    body = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["connection", "key"], name="switchboard_one_answer_per_key")]


class Delivery(models.Model):
    PENDING, DELIVERED, FAILED, GONE = "pending", "delivered", "failed", "gone"
    STATUSES = [(PENDING, "Trying"), (DELIVERED, "Delivered"), (FAILED, "Failed"), (GONE, "Connection gone")]
    connection = models.ForeignKey(SwitchboardConnection, on_delete=models.CASCADE, related_name="deliveries")
    event_id = models.CharField(max_length=40, db_index=True)
    event_type = models.CharField(max_length=60)
    summary = models.CharField(max_length=140, blank=True)
    body = models.TextField(blank=True, help_text="The exact JSON posted; cleared once the delivery is over")
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["connection", "event_id"], name="switchboard_one_delivery_per_event")]


class IssuedCard(models.Model):
    connection = models.ForeignKey(SwitchboardConnection, null=True, blank=True, on_delete=models.SET_NULL, related_name="issued_cards")
    host = models.CharField(max_length=255, blank=True, help_text="That connection's host: a Switchboard that reconnects still finds its cards")
    card = models.OneToOneField("gift_cards.StaffCard", on_delete=models.CASCADE, related_name="switchboard_issue")
    created_at = models.DateTimeField(default=timezone.now)
