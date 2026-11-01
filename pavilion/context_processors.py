from django.conf import settings


def browser_errors(request):
    """`browser_errors` in every template: the settings pos_server/_browser_errors.html needs to send this page's
    JavaScript errors to Bugsink (the same DSN as the server's), or None where reporting is off (DEBUG, tests)."""
    if not settings.BUGSINK_DSN or settings.DEBUG or not settings.BUGSINK_BROWSER:
        return {"browser_errors": None}
    config = {"dsn": settings.BUGSINK_DSN, "environment": settings.ENVIRONMENT_NAME}
    user = getattr(request, "user", None)
    if settings.BUGSINK_SEND_PII and user is not None and user.is_authenticated:
        config["user"] = {"id": user.pk, "username": user.get_username()}
    return {"browser_errors": config}
