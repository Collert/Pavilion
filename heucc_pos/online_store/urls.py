from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="store_home"),
    path("menu", views.menu, name="menu"),
    path("dish/<int:id>", views.dish, name="dish"),
    path("submit-order", views.place_order, name="submit_order"),
    path("order-status/<int:id>", views.order_status, name="order_status"),
]