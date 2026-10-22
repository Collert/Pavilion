from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="audio_index"),
    path("music/<int:id>", views.music_channel, name="audio_music"),
    path("voice/<int:id>", views.voice_channel, name="audio_voice"),
    path("channels/<int:id>/now", views.now, name="audio_now"),
    path("channels/<int:id>/control", views.control, name="audio_control"),
    path("talk/<int:id>", views.talk, name="audio_talk"),
]
