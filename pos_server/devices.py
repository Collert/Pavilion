"""
Server-side enforcement of the registered-device allowlist.

A superuser registers a device (``device_elig`` POST). The device then proves itself with the token kept in
its localStorage (``device_elig`` PUT), and the server answers with a signed, HttpOnly cookie. Views wrapped in
``device_required`` check that cookie, so the check no longer depends on client-side JS.
"""
import uuid
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode, url_has_allowed_host_and_scheme

from .models import EligibleDevice

DEVICE_COOKIE = "pavilion_device"
DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def set_device_cookie(response, token):
    response.set_signed_cookie(
        DEVICE_COOKIE, str(token),
        max_age=DEVICE_COOKIE_MAX_AGE, httponly=True, samesite="Lax", secure=not settings.DEBUG,
    )
    return response


def token_is_registered(token) -> bool:
    try:
        return EligibleDevice.objects.filter(token=uuid.UUID(str(token))).exists()
    except (ValueError, AttributeError, TypeError):
        return False


def is_registered_device(request) -> bool:
    if not settings.ENFORCE_DEVICE_CHECK:
        return True
    token = request.get_signed_cookie(DEVICE_COOKIE, default=None)
    return token is not None and token_is_registered(token)


def safe_next_url(request, candidate):
    """Only allow redirects back to this site."""
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return None


def device_required(view):
    """
    Reject requests from unregistered devices. Page loads are redirected to the registration page,
    fetch() calls get a 403 JSON response.
    """
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if is_registered_device(request):
            return view(request, *args, **kwargs)
        if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
            return redirect(f'{reverse("device_elig")}?{urlencode({"next": request.get_full_path()})}')
        return JsonResponse({"error": "Device not authorized"}, status=403)
    return wrapper


def staff_required(view):
    """Logged in *and* staff. Customers can register accounts, so login_required alone isn't enough."""
    return login_required(user_passes_test(lambda u: u.is_staff)(view))


def superuser_required(view):
    return login_required(user_passes_test(lambda u: u.is_superuser)(view))


def api_staff_required(view):
    """For fetch() endpoints: 401/403 JSON instead of a redirect to the login page."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required"}, status=401)
        if not request.user.is_staff:
            return JsonResponse({"error": "Staff only"}, status=403)
        return view(request, *args, **kwargs)
    return wrapper
