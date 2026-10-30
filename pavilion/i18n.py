"""The JavaScript translation catalog, one address per language (/jsi18n/<lang>/), so browsers can keep each for a day.
Templates link it with {% url 'javascript-catalog' LANGUAGE_CODE %}."""
from django.http import Http404
from django.utils import translation
from django.views.decorators.cache import cache_control
from django.views.i18n import JavaScriptCatalog

from users.models import enabled_language

_catalog = JavaScriptCatalog.as_view()


@cache_control(public=True, max_age=24 * 3600)
def javascript_catalog(request, lang):
    code = enabled_language(lang)
    if not code:
        raise Http404
    with translation.override(code):
        return _catalog(request)
