from .access import Can


def access(request):
    """`can` in every template: `{% if can.app_register %}` (users/access.py)."""
    return {"can": Can(getattr(request, "user", None))}
