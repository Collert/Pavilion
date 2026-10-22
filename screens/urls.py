from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="screens"),
    path("announce", views.announce, name="screens_announce"),
    path("<slug:slug>", views.show, name="screen"),
]
