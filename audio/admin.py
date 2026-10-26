from django.contrib import admin

from .models import AudioChannel, Playlist, PlaylistTrack, Track


class PlaylistTrackInline(admin.TabularInline):
    model = PlaylistTrack
    extra = 1


@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    inlines = [PlaylistTrackInline]


admin.site.register(Track)
admin.site.register(AudioChannel)
