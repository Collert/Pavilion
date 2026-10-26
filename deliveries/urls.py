from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="delivery_orders"),
    path("shift", views.shift, name="delivery_shift"),
    path("order/<int:id>", views.order, name="delivery_order"),
    path("current", views.current, name="delivery_current"),
    path("done/<int:id>", views.done, name="delivery_done"),
    path('vapid-public-key/', views.vapid_public_key, name='deliveries_vapid_public_key'),
    path('save-subscription/', views.save_subscription, name='deliveries_save_subscription'),
]
