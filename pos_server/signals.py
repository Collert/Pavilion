from django.dispatch import Signal

# Sent with `order` when the last station working on an order finishes it.
order_ready = Signal()


def connect_realtime():
    """Publish SSE nudges when anything the screens show changes (see pavilion/realtime.py)."""
    from django.db.models.signals import post_delete, post_save
    from pavilion.realtime import publish
    from .models import (Combo, ComboSlot, ComboSlotOption, Component, Dish, DishComponent, Extra, Ingredient, Option,
                         OptionGroup, Order, OrderStation)

    def orders_changed(sender, **kwargs):
        publish("orders")

    def inventory_changed(sender, **kwargs):
        publish("inventory")

    for model in (Order, OrderStation):
        post_save.connect(orders_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-save")
        post_delete.connect(orders_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-delete")
    # Stock (a dish or component running out or coming back) and anything else the register's catalog shows
    for model in (Dish, Component, Ingredient, DishComponent, OptionGroup, Option, Extra, Combo, ComboSlot, ComboSlotOption):
        post_save.connect(inventory_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-save")
        post_delete.connect(inventory_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-delete")
    # Cookery: batches in progress (timers, steps, counts) shared between screens, and the recipes behind them
    from inventory.models import CookingSession, Recipe, RecipeIngredient, RecipeStep, Transformation, YieldEntry

    def cookery_changed(sender, **kwargs):
        publish("cookery")

    for model in (CookingSession, YieldEntry, Recipe, RecipeIngredient, RecipeStep, Transformation):
        post_save.connect(cookery_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-save")
        post_delete.connect(cookery_changed, sender=model, dispatch_uid=f"realtime-{model.__name__}-delete")
