from django.urls import path

from . import views

urlpatterns = [
    path("pair", views.pair, name="dongle_pair"),
    path("open/<str:value>", views.open_screen, name="dongle_open"),
]
