from django.urls import path

from . import views

urlpatterns = [
    path("<uuid:token>", views.order_page, name="catering_order"),
]
