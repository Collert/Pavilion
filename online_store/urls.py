from django.urls import path

from . import account, app, pages, views, work

urlpatterns = [
    path("", views.index, name="store_home"),
    path("menu", pages.menu_page, name="menu"),
    path("dish/<int:id>", pages.dish_redirect, name="dish"),
    path("cart", pages.checkout, name="store_cart"),
    path("services", pages.services_page, name="store_services"),
    path("gift-cards", pages.gift_cards_page, name="store_gift_cards"),
    path("order-status/<int:id>", pages.order_page, name="order_status"),
    path("order-status/<int:id>/<str:from_placing>", pages.order_page, name="order_status"),
    path("account", account.orders, name="account"),
    path("account/catering", account.catering, name="account_catering"),
    path("account/profile", account.profile, name="account_profile"),
    path("account/shifts", work.shifts, name="account_shifts"),
    path("account/up-for-grabs", work.market, name="account_market"),
    path("account/time", work.time_page, name="account_time"),
    path("account/orders/<int:order_id>/reorder", account.reorder, name="account_reorder"),
    path("account/orders/<int:order_id>/receipt", account.receipt, name="account_receipt"),
    path("account/sign-in", account.signin, name="store_signin"),
    path("account/join", account.join, name="store_join"),
    path("account/sign-out", account.signout, name="store_signout"),
    # The installable store app (online_store/app.py): its scope is this whole URLconf
    path("install", app.install, name="store_install"),
    path("manifest.webmanifest", app.manifest, name="store_manifest"),
    path("icon-<int:size>.png", app.icon, name="store_icon"),
    path("icon-maskable.png", app.icon_maskable, name="store_icon_maskable"),
    path("sw.js", app.service_worker, name="store_worker"),
    path("push/renew", app.renew, name="store_push_renew"),
    path("push/sync", app.push_sync, name="store_push_sync"),
    path("push/forget", app.push_forget, name="store_push_forget"),
    path("push/order/<int:id>", app.push_order, name="store_push_order"),
]
