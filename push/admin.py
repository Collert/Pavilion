from django.contrib import admin

from .models import NotificationSwitch, PushSubscription


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("app", "user", "device", "language", "topics", "created_at", "last_success_at", "failures")
    list_filter = ("app",)
    search_fields = ("user__username", "user__email", "device")
    raw_id_fields = ("user",)


admin.site.register(NotificationSwitch)
