from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("login", views.login_view, name="login_view"),
    path("logout", views.logout_view, name="logout_view"),
    path("register", views.pos, name="pos"),
    path("pos-output", views.pos_out_display, name="pos_output"),
    path("kitchen", views.kitchen, name="kitchen"),
    path("bar", views.bar, name="bar"),
    path('order_updates', views.order_updates, name='order_updates'),
    path('menu_select', views.menu_select, name='menu_select'),
    path('create-menu', views.create_menu, name='create_menu'),
    path('dashboard', views.dashboard, name='dashboard'),
    path('day_stats', views.day_stats, name='day_stats'),
    path('pair-terminal', views.pair_square_terminal, name='pair-terminal'),
    path('webhook/square', views.square_webhook, name='square_webhook'),
    path('webhook/square/check_card_status', views.check_card_status, name='check_card_status'),
    path('check-su', views.check_superuser_status, name='check_su'),
]