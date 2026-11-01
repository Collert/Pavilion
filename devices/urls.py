from django.urls import path

from . import views

urlpatterns = [
    path("dongles/pair", views.pair, name="dongle_pair"),
    path("dongles/open/<str:value>", views.open_screen, name="dongle_open"),
]
