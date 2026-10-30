from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="delivery_orders"),
    path("shift", views.shift, name="delivery_shift"),
    path("order/<int:id>", views.order, name="delivery_order"),
    path("current", views.current, name="delivery_current"),
    path("done/<int:id>", views.done, name="delivery_done"),
    # The installable courier app: its manifest and service worker (at the top of its scope), and push
    path("manifest.webmanifest", views.manifest, name="delivery_manifest"),
    path("sw.js", views.service_worker, name="delivery_worker"),
    path("push/renew", views.renew_subscription, name="delivery_push_renew"),
    path('save-subscription/', views.save_subscription, name='deliveries_save_subscription'),
]
