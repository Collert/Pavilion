from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="admin-index"),

    path("dashboard", views.dashboard, name="admin-dashboard"),
    path("dashboard/home", views.dashboard_home, name="admin-dashboard-home"),
    
    path("store", views.store, name="admin-store"),
    path("store/branding", views.store_branding, name="admin-store-branding"),
]