import re

from django.db import migrations, models


def to_list(apps, schema_editor):
    Track = apps.get_model("audio", "Track")
    for track in Track.objects.exclude(genre=""):
        genres = {}
        for part in re.split(r"[,;|\0]|\s/\s", track.genre):  # as audio.library.genres_of splits them
            name = " ".join(part.split())[:40]
            if name:
                genres.setdefault(name.casefold(), name)
        track.genres = list(genres.values())[:8]
        track.save(update_fields=["genres"])


def to_text(apps, schema_editor):
    Track = apps.get_model("audio", "Track")
    for track in Track.objects.all():
        track.genre = ", ".join(track.genres or [])[:80]
        track.save(update_fields=["genre"])


class Migration(migrations.Migration):

    dependencies = [
        ("audio", "0004_alter_track_artwork_alter_track_file"),
    ]

    operations = [
        migrations.AddField(
            model_name="track",
            name="genres",
            field=models.JSONField(blank=True, default=list, help_text='["Jazz", "Soul"]: from the tags, or typed in (audio.library.genres_of)'),
        ),
        migrations.RunPython(to_list, to_text),
        migrations.RemoveField(model_name="track", name="genre"),
    ]
