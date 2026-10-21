from django.contrib import admin

from .models import Booking, Service


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "pricing", "price", "active", "archived")
    list_filter = ("category", "active", "archived")
    search_fields = ("title",)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("service", "name", "date", "status", "total")
    list_filter = ("status", "service__category")
    search_fields = ("name", "service__title")
