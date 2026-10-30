from django.urls import path

from . import listmonk

urlpatterns = [
    path("listmonk", listmonk.receive, name="listmonk_messenger"),  # campaigns from listmonk (push/listmonk.py)
]
