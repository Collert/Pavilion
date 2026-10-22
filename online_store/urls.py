from django.urls import path

from . import pages, views

urlpatterns = [
    path("", views.index, name="store_home"),
    path("menu", pages.menu_page, name="menu"),
    path("dish/<int:id>", pages.dish_redirect, name="dish"),
    path("cart", pages.checkout, name="store_cart"),
    path("submit-order", views.place_order, name="submit_order"),
    path("cart-quote", views.cart_quote, name="cart_quote"),
    path("order-status/<int:id>", pages.order_page, name="order_status"),
    path("order-status/<int:id>/<str:from_placing>", pages.order_page, name="order_status"),
    path("order-history", views.order_history, name="order_history"),
    path('service-worker.js', views.service_worker_view, name='service_worker_serve'),
]
