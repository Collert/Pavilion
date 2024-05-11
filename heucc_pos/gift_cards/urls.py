from django.urls import path

from . import views

urlpatterns = [
    path("card/<int:card_number>", views.card_display, name="card_display"),
]