from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="screens"),
    path("<slug:slug>", views.show, name="screen"),
]
