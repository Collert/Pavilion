from django.urls import path

from . import views

urlpatterns = [
    path("", views.ready_orders, name="delivery_orders"),
    path("profile", views.profile, name="delivery_profile"),
    path("eta", views.get_eta, name="eta"),
    path("order/<int:id>", views.order, name="delivery_order"),
    path('vapid-public-key/', views.vapid_public_key, name='deliveries_vapid_public_key'),
    path('save-subscription/', views.save_subscription, name='deliveries_save_subscription'),
]