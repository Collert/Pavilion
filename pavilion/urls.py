"""
URL configuration for pavilion project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.templatetags.static import static as static_url
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.conf.urls.static import static
from django.conf import settings
from pavilion import realtime, redirects
from pavilion.i18n import javascript_catalog

from admin_portal import views as admin_views  # noqa: E402
from pos_server import views as pos_views  # noqa: E402

# No language prefixes: the language is the cookie the pickers set (i18n/), else the browser's (LocaleMiddleware).
urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=static_url('pavilion/favicon-32.png'), permanent=True)),  # Pavilion's cloche
    path('events/<str:topic>', realtime.stream_view, name='events'),
    path('i18n/', include('django.conf.urls.i18n')),  # set_language: the language pickers post here
    # Square needs fixed addresses: the OAuth redirect URL and the webhook
    path('square/oauth/callback', admin_views.square_oauth_callback, name='square_oauth_callback'),
    path('square/webhook', pos_views.square_webhook, name='square_webhook_plain'),
    # The translations for gettext() in our scripts. The language is in the address so browsers can keep each one for
    # a day instead of fetching it again on every page.
    path('jsi18n/<str:lang>/', javascript_catalog, name='javascript-catalog'),
    # The online store, and catering's customer pages in its look: everything the installed store app opens
    path('store/', include("online_store.urls")),
    path('store/catering/', include("catering.urls")),
    path("admin/", include("admin_portal.urls")),
    path('advanced-admin/', admin.site.urls),
    path('restaurant/', include("pos_server.urls")),
    path('deliveries/', include("deliveries.urls")),
    path('utils/', include("misc_tools.urls")),
    path('inventory/', include("inventory.urls")),
    path('gift-cards/', include("gift_cards.urls")),
    path('payments/', include("payments.urls")),
    path('apps/', include("app_switcher.urls")),
    path('users/', include("users.urls")),
    path("screens/", include("screens.urls")),
    path("audio/", include("audio.urls")),
    path("team/", include("team.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    # Dev only; in production media lives in the S3 bucket (see settings.STORAGES).
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Last: old addresses (a language prefix, the store at the root) move to where those pages are now (pavilion/redirects.py)
_prefixes = "|".join(code.lower() for code, _name in settings.SUPPORTED_LANGUAGES)
urlpatterns += [
    re_path(rf"^(?P<lang>{_prefixes})(?:/(?P<rest>.*))?$", redirects.old_language_prefix, name="old_language_prefix"),
    re_path(r"^(?P<rest>.*)$", redirects.old_store_path, name="old_store_path"),
]
