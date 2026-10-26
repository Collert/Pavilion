"""Test helpers for users/access.py: staff open nothing until a group says so."""
from django.contrib.auth.models import Group

from .access import ACTIONS, APPS
from .models import GroupInfo


def crew(user, *codes):
    """Put a staff user in a group giving `codes` (default: every app and action except typed-in discounts and the
    admin) and return the user."""
    codes = list(codes) or [c for c, *_rest in APPS + ACTIONS if c not in ("act.discount", "app.admin")]
    group = Group.objects.create(name=f"Crew {Group.objects.count() + 1}")
    GroupInfo.objects.create(group=group, perms=codes)
    user.groups.add(group)
    if hasattr(user, "_pavilion_perms"):
        del user._pavilion_perms
    return user
