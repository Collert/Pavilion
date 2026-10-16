from django.contrib import admin
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from . import registry
from .models import Screen


@admin.register(Screen)
class ScreenAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "public", "open_link", "updated_at")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("open_link", "widget_reference")
    fieldsets = (
        (None, {"fields": ("name", "slug", "public", "open_link")}),
        (_("Canvas"), {"fields": (("width", "height"), "background", "padding")}),
        (_("Layout"), {"fields": ("layout", "widget_reference")}),
    )

    @admin.display(description=_("Open"))
    def open_link(self, screen):
        if not screen.pk:
            return "—"
        url = reverse("screen", kwargs={"slug": screen.slug})
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, url)

    @admin.display(description=_("Widgets"))
    def widget_reference(self, screen):
        return render_to_string("screens/widget_reference.html", {"widgets": registry.widgets().values()})
