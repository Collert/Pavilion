"""The first screen: the pickup order board from docs/mockups/screens/OrderBoard-html, at /screens/order-board."""
from django.db import migrations

ORDER_BOARD = {
    "widget": "rows",
    "params": {"count": 2, "weights": [1, "132px"], "gap": 24},
    "children": [
        {
            "widget": "columns",
            "params": {"count": 3, "weights": [1, 1.18, 0.92], "gap": 24},
            "children": [
                {"widget": "order_list", "params": {
                    "show": "preparing", "title": "Preparing", "subtitle": "We're making these now", "icon": "pot",
                    "theme": "light", "limit": 12,
                }},
                {"widget": "order_list", "params": {
                    "show": "ready", "title": "Ready for pickup", "subtitle": "Please collect at the counter",
                    "icon": "bag", "theme": "dark", "limit": 8,
                    "footer": "Show your order number at the counter. Online orders: it's in your confirmation email.",
                }},
                {"widget": "text_card", "params": {
                    "tag": "Best value", "title": "Lunch combo",
                    "text": "Any main, a side and a drink. Every day from 11 till 4.", "footer": "$18.95",
                    "background": "#FBE7B5",
                }},
            ],
        },
        {
            "widget": "columns",
            "params": {"count": 3, "weights": ["268px", "268px", 1], "gap": 24},
            "children": [
                {"widget": "clock"},
                {"widget": "weather"},
                {"widget": "now_playing"},
            ],
        },
    ],
}


def create(apps, schema_editor):
    Screen = apps.get_model("screens", "Screen")
    Screen.objects.get_or_create(slug="order-board", defaults={"name": "Order board", "layout": ORDER_BOARD, "public": True})


class Migration(migrations.Migration):
    dependencies = [("screens", "0001_initial")]

    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
