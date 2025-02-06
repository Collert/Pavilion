from django.urls import path

from . import views

urlpatterns = [
    path("post_offer", views.post_offer, name="post_offer"),
    path("get_answer", views.get_answer, name="get_answer"),
]