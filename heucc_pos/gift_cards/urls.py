from django.urls import path

from . import views

urlpatterns = [
    path("card/<int:card_number>", views.card_display, name="card_display"),
    path("get-card", views.get_card, name="get-card"),
    path("special", views.get_card, name="get-card"),
    path("card-confirm", views.new_card_confirmation, name="card_confirm"),
    path("email_card-confirm", views.email_card_confirmation, name="email_card-confirm"),
]