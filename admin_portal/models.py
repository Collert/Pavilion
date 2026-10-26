import hashlib
from decimal import Decimal

from django.core.cache import cache
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _


class StoreSettings(models.Model):
    """
    Settings for the whole store (one row). Store info (admin_portal/store_info.py) edits the store's details, tax,
    time zone, music space and service keys; the Online store page edits the details too, as a draft it publishes;
    Languages and Email their own. The admin rail shows the name.
    Use StoreSettings.load(), never create rows by hand: it's read from the cache (Redis in production), which saving
    clears, so pages don't ask the database for it on every request. A queryset .update() skips that: use save().

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
    listmonk_users_list = models.PositiveIntegerField(null=True, blank=True, help_text="The list everyone with an account is in (transactional only)")
    logo = models.ImageField(upload_to="branding", null=True, blank=True)
    address = models.CharField(max_length=120, blank=True, help_text="Street address, e.g. 1200 Main St")
    city = models.CharField(max_length=120, blank=True, help_text="City, province and postal code")
    phone = models.CharField(max_length=40, blank=True)
    directions = models.URLField(blank=True, help_text="A map link; empty = a map search for the address")
    theme = models.CharField(max_length=40, default="neighbourhood", help_text="The online store's live theme")
    draft = models.JSONField(default=dict, blank=True, help_text="Store details not published yet (Branding)")
    # Store info (admin_portal/store_info.py)
    email = models.EmailField(blank=True, help_text="Where customers and services can reach the store")
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"),
                                   help_text="Sales tax in percent, charged on orders after discounts (5 = 5%)")
    time_zone = models.CharField(max_length=60, default="America/Vancouver",
                                 help_text="The store's time zone (applied when Pavilion starts)")
    music_storage_gb = models.DecimalField(max_digits=7, decimal_places=1, default=Decimal("10"),
                                           help_text="How much space the music library may use, in GB")
    google_key = models.TextField(blank=True, help_text="Google Maps API key, encrypted (payments/secrets.py)")
    weather_key = models.TextField(blank=True, help_text="weatherapi.com key, encrypted (payments/secrets.py)")

    class Meta:
        verbose_name = _("store settings")
        verbose_name_plural = _("store settings")

    def __str__(self):
        return self.name

    @classmethod
    def cache_key(cls) -> str:
        # the fields are in the key, so a row cached before a migration isn't handed out after it (a stable hash:
        # every worker has to agree on the key)
        fields = ",".join(f.attname for f in cls._meta.concrete_fields)
        return "store-settings:" + hashlib.md5(fields.encode(), usedforsecurity=False).hexdigest()[:10]

    @classmethod
    def load(cls) -> "StoreSettings":
        key = cls.cache_key()
        settings = cache.get(key)
        if settings is None:
            settings, _created = cls.objects.get_or_create(pk=1)
            cache.set(key, settings, timeout=None)
        return settings

    @classmethod
    def forget(cls):
        """Drop the cached row: now, and again once the transaction commits (a request in between could cache the
        row as it was)."""
        key = cls.cache_key()
        cache.delete(key)
        transaction.on_commit(lambda: cache.delete(key))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.forget()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        self.forget()
        return result

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
