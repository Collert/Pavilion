from django.urls import path

from . import views

urlpatterns = [
    path("channels/<int:id>/now", views.now, name="audio_now"),
    path("channels/<int:id>/control", views.control, name="audio_control"),
    path("library/scan", views.rescan, name="audio_scan"),
]
