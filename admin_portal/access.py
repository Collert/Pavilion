"""
Who may open each admin page and what they may do there (users/access.py: `app.admin` plus `admin.<page>.<level>`).

    @admin_page("dishes", edit={"save", "duplicate"}, delete={"archive", "restore"})

GET needs view. A POST's `op` (the part before ":") needs delete when it's in `delete`, edit when it's in `edit` (or
`edit="*"`: every POST writes), view otherwise: the other ops only change the page's draft, which is never saved
without Save. Posting a file needs edit. Refused posts come back to the page with a message; a page that can't be
viewed sends people to the first one they can, or says they can't open the admin.
"""
from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _, gettext_lazy

from pos_server.devices import no_access, safe_next_url, staff_required
from users.access import can, can_page


def page_url(page):
    """The page's own address (its rail entry)."""
    from .nav import MENU
    from django.urls import reverse
    return next((reverse(url_name) for _g, _l, _p, pages in MENU for p, _pl, url_name in pages if p == page and url_name), None)


def first_page(user):
    """The url of the first rail page this user may view, or None."""
    from .nav import MENU
    from django.urls import reverse
    for _group, _label, _path, pages in MENU:
        for page_id, _page_label, url_name in pages:
            if url_name and can_page(user, page_id):
                return reverse(url_name)
    return None


def needed(request, edit=(), delete=()) -> str:
    """The level this request needs: view, edit or delete."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return "view"
    op = request.POST.get("op", "").partition(":")[0]
    if op in delete:
        return "delete"
    if edit == "*" or op in edit or request.FILES:
        return "edit"
    return "view"


REFUSED = {"edit": gettext_lazy("You can see this page, but your groups don’t let you change it."),
           "delete": gettext_lazy("Your groups don’t let you delete or archive here.")}


def admin_page(page, edit=(), delete=(), action=None, json=False):
    """Gate an admin view (see the module docstring). `action` is a further code it needs (e.g. "act.refund");
    `json` views answer refusals with a 403."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not (user.is_authenticated and user.is_staff):
                return staff_required(view)(request, *args, **kwargs)  # off to sign in
            if not can(user, "app.admin"):
                return no_access(request, "app.admin")
            level = needed(request, edit, delete)
            if can_page(user, page, level) and (action is None or can(user, action)):
                request.admin_page = page
                return view(request, *args, **kwargs)
            message = str(REFUSED.get(level, _("Your groups don’t include this page.")))
            if action and can_page(user, page, level):
                message = _("Your groups don’t let you do that.")
            if json:
                return JsonResponse({"error": message}, status=403)
            if request.method != "GET" and can_page(user, page):
                messages.error(request, message)  # back where they pressed it
                return redirect(safe_next_url(request, request.headers.get("Referer")) or page_url(page) or request.get_full_path())
            elsewhere = first_page(user)
            if elsewhere and request.method == "GET":
                return redirect(elsewhere)
            return no_access(request, "app.admin")
        return wrapper
    return decorator


def page_perms(user, page) -> dict:
    """What the layout needs to hide the buttons this person can't use: {"edit": bool, "delete": bool}."""
    return {"edit": can_page(user, page, "edit"), "delete": can_page(user, page, "delete")}
