"""
Language for people who are signed in, around Django's LocaleMiddleware (which picks it from the URL's prefix, then
the language cookie, then the browser). Before it: someone signed in without a language cookie starts in their saved
language (users.Preference), and when the store doesn't match devices (the admin's Languages page) the browser's
language is ignored, so they get the store's default. After it: the language they browse in is saved as theirs, so
their emails follow it.
"""
from django.conf import settings
from django.utils.translation import get_language_from_path

from .models import enabled_language, set_language


class LanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "LANGUAGE_MATCH_DEVICE", True):
            request.META.pop("HTTP_ACCEPT_LANGUAGE", None)
        user = request.user
        prefixed = get_language_from_path(request.path_info)  # the URL says which: nothing to look up
        if not prefixed and user.is_authenticated and settings.LANGUAGE_COOKIE_NAME not in request.COOKIES:
            saved = enabled_language(getattr(getattr(user, "preference", None), "language", ""))
            if saved:
                request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = saved
        response = self.get_response(request)
        browsing = enabled_language(prefixed) if prefixed else ""
        if (browsing and user.is_authenticated and request.method == "GET" and response.status_code == 200
                and request.session.get("language") != browsing):
            saved = enabled_language(getattr(getattr(user, "preference", None), "language", ""))
            if saved != browsing and (saved or browsing != settings.LANGUAGE_CODE):  # nothing to keep for the default
                set_language(user, browsing)
            request.session["language"] = browsing  # once a session (and on each change), not on every page
        return response
