from django.contrib import admin

from .models import Dongle, DonglePairingCode


@admin.register(Dongle)
class DongleAdmin(admin.ModelAdmin):
    list_display = ("name", "hardware_id", "screen", "orientation", "last_seen", "paired_at")
    readonly_fields = ("token_hash", "info", "paired_at", "paired_by", "last_seen")


@admin.register(DonglePairingCode)
class DonglePairingCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "created_by", "created_at", "expires_at", "used_at", "dongle")
