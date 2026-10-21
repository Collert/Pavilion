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
    path("menus", views.menus, name="admin_menus"),
    path("menus/new", views.menu_new, name="admin_menu_new"),
    path("menus/<int:menu_id>", views.menu_edit, name="admin_menu"),
    path("menus/<int:menu_id>/action", views.menu_action, name="admin_menu_action"),
    path("dishes", views.dishes, name="admin_dishes"),
    path("dishes/new", views.dish_new, name="admin_dish_new"),
    path("dishes/<int:dish_id>", views.dish_edit, name="admin_dish"),
    path("recipes", views.recipes, name="admin_recipes"),
    path("recipes/new", views.recipe_new, name="admin_recipe_new"),
    path("recipes/<int:recipe_id>", views.recipe_edit, name="admin_recipe"),
    path("combos", views.combos, name="admin_combos"),
    path("combos/new", views.combo_new, name="admin_combo_new"),
    path("combos/<int:combo_id>", views.combo_edit, name="admin_combo"),
    path("services", views.services, name="admin_services"),
    path("services/new", views.service_new, name="admin_service_new"),
    path("services/<int:service_id>", views.service_edit, name="admin_service"),
    path("components", views.components, name="admin_components"),
    path("components/new", views.component_new, name="admin_component_new"),
    path("components/<int:component_id>", views.component_edit, name="admin_component"),
]
