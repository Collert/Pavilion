from django.urls import path

from . import views

urlpatterns = [
    path("get-image-colors/", views.dom_image_colors, name="utils_get_image_colors"),
    path("tts", views.tts, name="utils_tts")
]