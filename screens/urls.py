from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="screens"),
    path("announce", views.announce, name="screens_announce"),
    path("pair", views.pair, name="screens_pair"),
    path("s/<str:code>/summary", views.summary, name="screen_summary"),
    path("<slug:slug>", views.show, name="screen"),
    path("<slug:slug>/sessions", views.sessions, name="screen_sessions"),
    path("<slug:slug>/s/<str:code>", views.show, name="screen_session"),
]
