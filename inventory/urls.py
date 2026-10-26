from django.urls import path

from . import cookery_views, views

urlpatterns = [
    path("log-shopping", views.log_shopping, name="log_shopping"),
    path("shopping-history", views.shopping_history, name="shopping_history"),
    path("day-display/<int:day_id>", views.day_display, name="day_display"),
    # Cookery (inventory/cookery_views.py)
    path("crafting", cookery_views.home, name="crafting"),
    path("crafting/transforms", cookery_views.home, {"mode": "transform"}, name="cookery_transforms"),
    path("crafting/start/<int:component_id>", cookery_views.start, name="cookery_start"),
    path("crafting/take/<int:session_id>", cookery_views.take, name="cookery_take"),
    path("crafting/recipe/<int:component_id>", cookery_views.recipe, name="cookery_recipe"),
    path("crafting/batch/<int:session_id>", cookery_views.batch, name="cookery_batch"),
    path("crafting/done/<int:session_id>", cookery_views.done, name="cookery_done"),
    path("crafting/transform/<int:transform_id>", cookery_views.transform, name="cookery_transform"),
    path("crafting/transforming/<int:session_id>", cookery_views.transforming, name="cookery_transforming"),
    path("component-availability", views.component_availability, name="component_availability"),
    path("availability/data", views.availability_data, name="availability_data"),
]