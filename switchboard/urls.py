"""Fixed addresses (Switchboard's docs/PROTOCOL.md), included at the site root."""
from django.urls import path, re_path

from . import views

urlpatterns = [
    path(".well-known/switchboard", views.manifest, name="switchboard_manifest"),
    path("switchboard/authorize", views.authorize, name="switchboard_authorize"),
    path("switchboard/v1/token", views.token, name="switchboard_token"),
    path("switchboard/v1/", views.ping, name="switchboard_api"),
    path("switchboard/v1", views.ping),
    path("switchboard/v1/actions/<str:name>", views.action, name="switchboard_action"),
    path("switchboard/v1/options/<str:name>", views.options, name="switchboard_options"),
    path("switchboard/v1/connection", views.connection_view, name="switchboard_connection"),
    # anything else under the API answers JSON, not the store's catch-all redirect at the end of the URLconf
    re_path(r"^switchboard/v1/(?P<rest>.*)$", views.unknown),
]
