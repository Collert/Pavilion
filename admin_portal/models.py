from django.db import models
from django.utils.translation import gettext_lazy as _


class StoreSettings(models.Model):
    """
    Settings for the whole store (one row). The Branding page edits these; the admin rail shows the name.
    Use StoreSettings.load(), never create rows by hand.

    The store details (name, logo, address, phone, directions) are what every online-store theme shows. Branding
    edits them in `draft` ({"name", "logo", "address", "city", "phone", "directions"}, logo = a media file name) and
    Publish copies them here, with `theme` = the theme customers see (online_store/themes.py).
    """
    name = models.CharField(_("store name"), max_length=60, default="My restaurant")
    # Languages (the admin's Languages page; admin_portal/languages.py applies them when Pavilion starts)
    languages = models.JSONField(default=list, blank=True, help_text="Language codes turned on; empty means all of them")
    default_language = models.CharField(max_length=10, default="en")
    match_device = models.BooleanField(default=True, help_text="Start people in their device's language when it's on")
    # Email through listmonk (the admin's Email page; misc_tools/listmonk.py). Empty: the LISTMONK_* settings, if any.
    listmonk_url = models.URLField(blank=True)
    listmonk_user = models.CharField(max_length=80, blank=True, help_text="An API user in listmonk")
    listmonk_token = models.CharField(max_length=200, blank=True)
    listmonk_from = models.CharField(max_length=160, blank=True, help_text="From address, e.g. Corner Kitchen <hello@example.com>")
    listmonk_list = models.PositiveIntegerField(null=True, blank=True, help_text="The customers list (newsletter)")
    logo = models.ImageField(upload_to="files/branding", null=True, blank=True)
    address = models.CharField(max_length=120, blank=True, help_text="Street address, e.g. 1200 Main St")
    city = models.CharField(max_length=120, blank=True, help_text="City, province and postal code")
    phone = models.CharField(max_length=40, blank=True)
    directions = models.URLField(blank=True, help_text="A map link; empty = a map search for the address")
    theme = models.CharField(max_length=40, default="neighbourhood", help_text="The online store's live theme")
    draft = models.JSONField(default=dict, blank=True, help_text="Store details not published yet (Branding)")

    class Meta:
        verbose_name = _("store settings")
        verbose_name_plural = _("store settings")

    def __str__(self):
        return self.name

    @classmethod
    def load(cls) -> "StoreSettings":
        settings, _created = cls.objects.get_or_create(pk=1)
        return settings

    def details(self) -> dict:
        """The published store details, in the same shape as `draft`."""
        return {"name": self.name, "logo": self.logo.name if self.logo else "", "address": self.address, "city": self.city,
                "phone": self.phone, "directions": self.directions}


class StoreTheme(models.Model):
    """One online-store theme's settings (its params, online_store/themes/<name>.json): live and being edited."""
    name = models.CharField(max_length=40, unique=True)
    live = models.JSONField(default=dict, blank=True)
    draft = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
