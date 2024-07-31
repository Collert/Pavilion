from django.urls import path

from . import views

urlpatterns = [
    path("web-payment-window", views.web_payment, name="web-payment-window"),
    path("process-web-payment", views.process_web_payment, name="process-web-payment"),
]