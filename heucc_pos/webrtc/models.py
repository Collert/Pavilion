from django.db import models
import uuid

class WebRTCSession(models.Model):
    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    offer = models.TextField(blank=True, null=True)
    answer = models.TextField(blank=True, null=True)
    ice_candidates = models.TextField(blank=True, null=True)  # JSON string

    def __str__(self):
        return str(self.session_id)
