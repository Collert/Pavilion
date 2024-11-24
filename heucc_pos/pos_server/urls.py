from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    # path("login", views.login_view, name="login_view"),
    # path("logout", views.logout_view, name="logout_view"),
    path("register", views.pos, name="pos"),
    path("pos-output", views.pos_out_display, name="pos_output"),
    path("kitchen", views.kitchen, name="kitchen"),
    path("order-marking", views.order_marking, name="order_marking"),
    path("bar", views.bar, name="bar"),
    path('menu_select', views.menu_select, name='menu_select'),
    path('create-menu', views.create_menu, name='create_menu'),
    path('dashboard', views.dashboard, name='dashboard'),
    path('day_stats', views.day_stats, name='day_stats'),
    path('pair-terminal', views.pair_square_terminal, name='pair-terminal'),
    path('webhook/square', views.square_webhook, name='square_webhook'),
    path('webhook/square/check_card_status', views.check_card_status, name='check_card_status'),
    path('check-su', views.check_superuser_status, name='check_su'),
    # path('register-staff', views.register_staff, name='register_staff'),
    path('orders-progress', views.order_progress, name='order_progress'),
    path('active_orders', views.active_orders, name='active_orders'),
    path('check_inventory', views.check_inventory, name='check_inventory'),
    path('check-device', views.device_elig, name='device_elig'),
]