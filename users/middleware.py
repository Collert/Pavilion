"""
Language for people who are signed in, around Django's LocaleMiddleware (which picks it from the language cookie the
pickers set, then the browser; addresses have no language prefix). Before it: someone signed in without a language
cookie starts in their saved language (users.Preference), and when the store doesn't match devices (the admin's
Languages page) the browser's language is ignored, so they get the store's default. After it: the language they picked
(the cookie) is saved as theirs, so their emails follow it.
"""
from django.conf import settings

from .models import enabled_language, set_language


class LanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "LANGUAGE_MATCH_DEVICE", True):
            request.META.pop("HTTP_ACCEPT_LANGUAGE", None)
        user = request.user
        picked = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if not picked and user.is_authenticated:
            if "language" not in request.session:  # looked up once a session
                request.session["language"] = enabled_language(getattr(getattr(user, "preference", None), "language", ""))
            if request.session["language"]:
                request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = request.session["language"]
        response = self.get_response(request)
        browsing = enabled_language(picked) if picked else ""
        if (browsing and user.is_authenticated and request.method == "GET" and response.status_code == 200
                and request.session.get("language") != browsing):
            saved = enabled_language(getattr(getattr(user, "preference", None), "language", ""))
            if saved != browsing and (saved or browsing != settings.LANGUAGE_CODE):  # nothing to keep for the default
                set_language(user, browsing)
            request.session["language"] = browsing  # once a session (and on each change), not on every page
        return response
