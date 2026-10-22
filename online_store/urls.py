from django.urls import path

from . import account, pages, views

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
    path("account", account.orders, name="account"),
    path("account/catering", account.catering, name="account_catering"),
    path("account/profile", account.profile, name="account_profile"),
    path("account/orders/<int:order_id>/reorder", account.reorder, name="account_reorder"),
    path("account/orders/<int:order_id>/receipt", account.receipt, name="account_receipt"),
    path("account/sign-in", account.signin, name="store_signin"),
    path("account/join", account.join, name="store_join"),
    path("account/sign-out", account.signout, name="store_signout"),
    path('service-worker.js', views.service_worker_view, name='service_worker_serve'),
]
