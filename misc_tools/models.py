from django.db import models


class EmailTemplate(models.Model):
    """Which listmonk template sends an email (misc_tools/emails.py: a kind) in a language. Made by
    misc_tools/listmonk.py: install, or picked on the admin's Email page. Kind "newsletter" is the campaign template."""
    kind = models.CharField(max_length=40)
    language = models.CharField(max_length=10)
    template_id = models.PositiveIntegerField()
    managed = models.BooleanField(default=True, help_text="Pavilion made it (Reset to default rewrites it); off when staff picked their own")

    class Meta:
        unique_together = [("kind", "language")]

    def __str__(self):
        return f"{self.kind} · {self.language} · #{self.template_id}"


class EmailSwitch(models.Model):
    """An email turned on or off on the admin's Email page (no row: its default)."""
    kind = models.CharField(max_length=40, unique=True)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.kind}: {'on' if self.enabled else 'off'}"
