from django.contrib import admin

from .models import CateringLine, CateringMessage, CateringOrder, CateringSettings


class LineInline(admin.TabularInline):
    model = CateringLine
    extra = 0


class MessageInline(admin.TabularInline):
    model = CateringMessage
    extra = 0


@admin.register(CateringOrder)
class CateringOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "title", "name", "event_date", "status")
    list_filter = ("status", "fulfilment")
    search_fields = ("name", "organization", "email", "title")
    inlines = [LineInline, MessageInline]


admin.site.register(CateringSettings)
