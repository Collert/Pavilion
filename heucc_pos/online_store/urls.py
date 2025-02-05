from django.urls import path
from django.views.generic import TemplateView

from . import views

urlpatterns = [
    path("", views.index, name="store_home"),
    path("menu", views.menu, name="menu"),
    path("dish/<int:id>", views.dish, name="dish"),
    path("submit-order", views.place_order, name="submit_order"),
    path("order-status/<int:id>", views.order_status, name="order_status"),
    path("order-status/<int:id>/<str:from_placing>", views.order_status, name="order_status"), 
    path("order-history", views.order_history, name="order_history"),
    path('service-worker.js', views.service_worker_view, name='service_worker_serve'),
]