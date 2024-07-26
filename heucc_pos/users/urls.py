from django.urls import path

from . import views

urlpatterns = [
    path("login", views.login_view, name="login_view"),
    path("logout", views.logout_view, name="logout_view"),
    path('register', views.register, name='register'),
    path('profile', views.profile, name='profile'),
]