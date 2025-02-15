from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="admin-index"),


    path("dashboard", views.dashboard, name="admin-dashboard"),
    path("dashboard/home", views.dashboard_home, name="admin-dashboard-home"),

    path("orders", views.orders, name="admin-orders"),
    path("orders/retail", views.retail_orders, name="admin-orders-retail"),
    path("orders/retail/<int:id>", views.retail_order, name="admin-order-retail"),
    path("orders/retail/<int:id>/receipt", views.receipt, name="admin-index"),
    
    path("store", views.store, name="admin-store"),
    path("store/branding", views.store_branding, name="admin-store-branding"),
]