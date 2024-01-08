from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="pos_login"),
    path("login", views.login_view, name="login_view"),
    path("register/<str:menu>", views.pos, name="pos"),
    path("kitchen", views.kitchen, name="kitchen"),
    path('order_updates', views.order_updates, name='order_updates'),
    path('menu_select', views.menu_select, name='menu_select'),
]