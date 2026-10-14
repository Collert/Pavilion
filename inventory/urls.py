from django.urls import path

from . import views

urlpatterns = [
    path("log-shopping", views.log_shopping, name="log_shopping"),
    path("shopping-history", views.shopping_history, name="shopping_history"),
    path("day-display/<int:day_id>", views.day_display, name="day_display"),
    path("crafting", views.crafting, name="crafting"),
    path("crafting/data", views.cookery_data, name="cookery_data"),
    path("component-availability", views.component_availability, name="component_availability"),
    path("availability/data", views.availability_data, name="availability_data"),
]