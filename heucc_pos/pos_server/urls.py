from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="pos_login"),
    path("login", views.login_view, name="login_view"),
    path("register/<str:menu>", views.pos, name="pos"),
    path("pos-output", views.pos_out_display, name="pos_output"),
    path("kitchen", views.kitchen, name="kitchen"),
    path("bar", views.bar, name="bar"),
    path('order_updates', views.order_updates, name='order_updates'),
    path('menu_select', views.menu_select, name='menu_select'),
    path('dashboard', views.dashboard, name='dashboard'),
    path('day_stats', views.day_stats, name='day_stats'),
    path('pair-terminal', views.pair_square_terminal, name='pair-terminal'),
    path('webhook/square/', views.square_webhook, name='square_webhook'),
]