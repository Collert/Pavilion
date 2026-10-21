from django.urls import path

from . import register, views

urlpatterns = [
    path("", views.index, name="index"),
    path("register", register.register, name="pos"),
    path("register/quote", register.quote, name="register_quote"),
    path("register/catalog", register.catalog, name="register_catalog"),
    path("register/card", register.card_checkout, name="register_card"),
    path("lock", register.lock, name="register_lock"),
    path("drawer", register.cash_drawer, name="cash_drawer"),
    path("pos-output", views.pos_out_display, name="pos_output"),
    path("order-marking", views.order_marking, name="order_marking"),
    path('menu_select', views.menu_select, name='menu_select'),
    path('create-menu', views.create_menu, name='create_menu'),
    path('day_stats', views.day_stats, name='day_stats'),
    path('pair-terminal', views.pair_square_terminal, name='pair-terminal'),
    path('webhook/square', views.square_webhook, name='square_webhook'),
    path('webhook/square/check_card_status', views.check_card_status, name='check_card_status'),
    # path('register-staff', views.register_staff, name='register_staff'),
    path('orders-progress', views.order_progress, name='order_progress'),
    path('active_orders', views.active_orders, name='active_orders'),
    path('check-device', views.device_elig, name='device_elig'),
]
