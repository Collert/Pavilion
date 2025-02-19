"""
URL configuration for pavilion project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
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
from django.urls import include, path
from django.conf.urls.static import static
from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.views.i18n import JavaScriptCatalog

urlpatterns = [
    path('jsi18n/', JavaScriptCatalog.as_view(), name='javascript-catalog')
]

urlpatterns += i18n_patterns(
    path('', include("online_store.urls")),
    path('admin/', include("new_admin.urls")),
    path('advanced-admin/', admin.site.urls),
    path('webrtc/', include("webrtc.urls")),
    path('restaurant/', include("pos_server.urls")),
    path('deliveries/', include("deliveries.urls")),
    path('utils/', include("misc_tools.urls")),
    path('inventory/', include("inventory.urls")),
    path('gift-cards/', include("gift_cards.urls")),
    path('payments/', include("payments.urls")),
    path('apps/', include("app_switcher.urls")),
    path('users/', include("users.urls")),
)

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)