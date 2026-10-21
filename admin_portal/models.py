from django.db import models
from django.utils.translation import gettext_lazy as _


class StoreSettings(models.Model):
    """
    Settings for the whole store (one row). The Branding page edits these; the admin rail shows the name.
    Use StoreSettings.load(), never create rows by hand.
    """
    name = models.CharField(_("store name"), max_length=60, default="My restaurant")

    class Meta:
        verbose_name = _("store settings")
        verbose_name_plural = _("store settings")

    def __str__(self):
        return self.name

    @classmethod
    def load(cls) -> "StoreSettings":
        settings, _created = cls.objects.get_or_create(pk=1)
        return settings
