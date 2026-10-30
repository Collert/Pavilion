"""
Old addresses that still work. Pages used to sit under a language prefix (/en/menu, /uk/apps/) and the online store at
the site's root (/menu, /order-status/12). Now nothing has a prefix (the language comes from the cookie the language
pickers set, else the browser) and the store lives under /store/, so the installed store app (its scope) holds only
the store. Both are moved here with a permanent redirect, keeping the query string:
- /uk/menu -> /store/menu, and the language becomes Ukrainian (it was in the address);
- /menu, /order-status/12, / -> /store/..., when that's a page; anything else is a 404 as before.
These patterns go last, so every real page is matched first.
"""
from django.conf import settings
from django.http import Http404, HttpResponsePermanentRedirect
from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme

from users.models import enabled_language


def _routable(path) -> bool:
    """Whether `path` is a page (not one of these redirects)."""
    try:
        match = resolve(path)
    except Resolver404:
        return False
    return match.url_name not in ("old_language_prefix", "old_store_path")


def target(path) -> str | None:
    """Where an old address went: the path as it is, with a slash added, or under /store/; None if nowhere."""
    rest = path.lstrip("/")
    candidates = [path, "/store/" + rest]
    if settings.APPEND_SLASH and not path.endswith("/"):
        candidates = [path, path + "/", "/store/" + rest, "/store/" + rest + "/"]
    return next((c for c in candidates if _routable(c)), None)


def _redirect(request, path):
    query = request.META.get("QUERY_STRING", "")
    to = path + ("?" + query if query else "")
    if not url_has_allowed_host_and_scheme(to, allowed_hosts=None):  # never off the site ("//evil.example")
        raise Http404
    return HttpResponsePermanentRedirect(to)


def old_language_prefix(request, lang, rest=""):
    path = target("/" + (rest or ""))
    if path is None:
        raise Http404
    response = _redirect(request, path)
    code = enabled_language(lang)
    if code:
        response.set_cookie(settings.LANGUAGE_COOKIE_NAME, code, max_age=settings.LANGUAGE_COOKIE_AGE,
                            path=settings.LANGUAGE_COOKIE_PATH, samesite=settings.LANGUAGE_COOKIE_SAMESITE)
    return response


def old_store_path(request, rest=""):
    path = target("/" + rest)
    if path is None or path == "/" + rest:
        raise Http404
    return _redirect(request, path)
