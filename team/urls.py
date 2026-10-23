"""The team app's pages: the time clock (team/clock_views.py)."""
from django.urls import path

from . import clock_views

urlpatterns = [
    path("clock", clock_views.time_clock, name="time_clock"),
]
