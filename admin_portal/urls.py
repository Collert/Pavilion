from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="admin_home"),
    path("reports", views.reports, name="admin_reports"),
    path("reports/latest", views.report_day, {"day": ""}, name="admin_report_last"),
    path("reports/<str:day>.csv", views.report_day_csv, name="admin_report_csv"),  # before the day page, which would match it
    path("reports/<str:day>", views.report_day, name="admin_report_day"),
]
