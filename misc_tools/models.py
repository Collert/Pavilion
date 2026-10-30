from django.db import models


class EmailTemplate(models.Model):
    """Which listmonk template sends an email (misc_tools/emails.py: a kind) in a language. Made by
    misc_tools/listmonk.py: install, or picked on the admin's Email page. Kind "newsletter" is the campaign template.
    A marketing email's campaign template holds every language: its language is ALL."""
    ALL = "all"

    kind = models.CharField(max_length=40)
    language = models.CharField(max_length=10)
    template_id = models.PositiveIntegerField()
    managed = models.BooleanField(default=True, help_text="Pavilion made it (Reset to default rewrites it); off when staff picked their own")

    class Meta:
        unique_together = [("kind", "language")]

    def __str__(self):
        return f"{self.kind} · {self.language} · #{self.template_id}"


class EmailSwitch(models.Model):
    """An email turned on or off on the admin's Email page (no row: its default), and for a marketing email the
    listmonk lists it also goes to besides the customers list."""
    kind = models.CharField(max_length=40, unique=True)
    enabled = models.BooleanField(default=True)
    lists = models.JSONField(default=list, blank=True, help_text="listmonk list ids a marketing email also goes to")

    def __str__(self):
        return f"{self.kind}: {'on' if self.enabled else 'off'}"
