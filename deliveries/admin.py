from django.contrib import admin
from django.utils.html import format_html

from .models import CourierShift, Delivery


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("order_number", "customer_name", "destination", "courier", "step", "delivered_at")
    list_filter = ("delivered_at", "handoff")
    search_fields = ("order_number", "customer_name", "destination")
    readonly_fields = ("proof",)
    filter_horizontal = ("passed_by",)

    @admin.display(description="Step")
    def step(self, obj):
        return obj.phase()

    @admin.display(description="Proof of delivery")
    def proof(self, obj):
        if not obj.photo:
            return "–"
        return format_html('<a href="{}" target="_blank"><img src="{}" alt="" style="max-width: 360px; border-radius: 12px"></a>',
                           obj.photo.url, obj.photo.url)


@admin.register(CourierShift)
class CourierShiftAdmin(admin.ModelAdmin):
    list_display = ("user", "mode", "started_at")

