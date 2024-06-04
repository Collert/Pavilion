from django.urls import path

from . import views

urlpatterns = [
    path("menu", views.menu, name="menu"),
    path("dish/<int:id>", views.dish, name="dish"),
]