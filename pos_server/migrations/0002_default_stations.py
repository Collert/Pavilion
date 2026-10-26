from django.db import migrations

DEFAULT_STATIONS = [
    # code, name, icon, menu heading, position
    ("kitchen", "Kitchen", "skillet", "Hot food", 0),
    ("bar", "Bar", "local_cafe", "Drinks and sweets", 1),
    ("gng", "Grab & Go", "kitchen", "Grab & Go", 2),
]


def create_default_stations(apps, schema_editor):
    """The three stations the app used to hard-code. Edit or add more in /advanced-admin/."""
    Station = apps.get_model("pos_server", "Station")
    for code, name, icon, heading, position in DEFAULT_STATIONS:
        Station.objects.get_or_create(code=code, defaults={
            "name": name, "icon": icon, "menu_heading": heading, "position": position,
        })


class Migration(migrations.Migration):
    dependencies = [("pos_server", "0001_initial")]
    operations = [migrations.RunPython(create_default_stations, migrations.RunPython.noop)]
