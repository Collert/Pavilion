from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="admin_home"),
    path("reports", views.reports, name="admin_reports"),
    path("reports/latest", views.report_day, {"day": ""}, name="admin_report_last"),
    path("reports/<str:day>.csv", views.report_day_csv, name="admin_report_csv"),  # before the day page, which would match it
    path("reports/<str:day>", views.report_day, name="admin_report_day"),
    path("orders", views.orders, name="admin_orders"),
    path("orders/<int:order_id>", views.order_detail, name="admin_order"),
    path("orders/<int:order_id>/note", views.order_note, name="admin_order_note"),
    path("orders/<int:order_id>/send", views.order_send, name="admin_order_send"),
    path("orders/<int:order_id>/refund", views.order_refund, name="admin_order_refund"),
    path("orders/<int:order_id>/print", views.order_print, name="admin_order_print"),
    path("orders/<int:order_id>/receipt", views.order_receipt, name="admin_order_receipt"),
]
