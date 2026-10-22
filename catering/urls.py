from django.urls import path

from . import views

urlpatterns = [
    path("", views.quote_page, name="store_catering"),
    path("<uuid:token>", views.order_page, name="catering_order"),
]
