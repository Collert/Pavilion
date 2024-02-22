from django.urls import path

from . import views

urlpatterns = [
    path("log-shopping", views.log_shopping, name="log_shopping"),
    path("shopping-history", views.shopping_history, name="shopping_history"),
    path("day-display/<int:day_id>", views.day_display, name="day_display"),
    path("crafting", views.crafting, name="crafting"),
    path("inventory-updates", views.inventory_updates, name="inventory_updates"),
    path("component-availability", views.component_availability, name="component_availability"),
    path("recipes", views.recipes, name="recipes"),
    path("recipes/<int:recipe_id>", views.recipe, name="recipe"),
]