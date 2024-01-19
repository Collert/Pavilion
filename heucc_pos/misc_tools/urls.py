from django.urls import path

from . import views

urlpatterns = [
    path("get-image-colors/", views.index, name="utils_get_image_colors")
]