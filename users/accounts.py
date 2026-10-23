"""
Account emails: confirming an email address, changing it, and a link to set a new password (sent from the store's
sign-up and the admin's Customers page). Links carry a token that stops working once it's used: the email one
depends on the address and whether it's confirmed (EmailTokens), the password one on the password (Django's).
"""
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from misc_tools.email import send

from .models import Customer


class EmailTokens(PasswordResetTokenGenerator):
    key_salt = "pavilion.users.accounts.EmailTokens"

    def _make_hash_value(self, user, timestamp):
        profile = Customer.objects.filter(user=user).first()
        return f"{user.pk}{timestamp}{user.email}{profile.pending_email if profile else ''}{profile.email_verified if profile else ''}"


email_tokens = EmailTokens()


def _store():
    from admin_portal.models import StoreSettings
    return StoreSettings.load().name


def _uid(user) -> str:
    return urlsafe_base64_encode(force_bytes(user.pk))


def profile_of(user) -> Customer:
    return Customer.objects.get_or_create(user=user)[0]


def send_verification(request, user):
    """Ask them to confirm their email (or the new one waiting in pending_email)."""
    profile = profile_of(user)
    to = profile.pending_email or user.email
    link = request.build_absolute_uri(reverse("verify_email", args=[_uid(user), email_tokens.make_token(user)]))
    return send("verify_email", to, {"name": user.first_name, "store": _store(), "link": link})


def change_email(request, user, new):
    """A staff member changed their email: the new address confirms it, the old one hears about it."""
    profile = profile_of(user)
    old = user.email
    profile.pending_email = new
    profile.save(update_fields=["pending_email"])
    link = request.build_absolute_uri(reverse("verify_email", args=[_uid(user), email_tokens.make_token(user)]))
    send("email_change_confirm", new, {"name": user.first_name, "store": _store(), "link": link})
    if old:
        send("email_change_notice", old, {"name": user.first_name, "store": _store(), "new": new})


def send_password_reset(request, user):
    link = request.build_absolute_uri(reverse("staff_welcome", args=[_uid(user), default_token_generator.make_token(user)]))
    return send("password_reset", user.email, {"name": user.first_name, "store": _store(), "link": link})


def confirm(user):
    """The link was opened: a waiting new email takes over (and the username, when it was the old email)."""
    from django.contrib.auth.models import User
    profile = profile_of(user)
    if profile.pending_email:
        new = profile.pending_email
        if user.username.lower() == (user.email or "").lower() and not User.objects.filter(username__iexact=new).exclude(pk=user.pk).exists():
            user.username = new.lower()[:150]
        user.email = new
        user.save(update_fields=["email", "username"])
        profile.pending_email = ""
    profile.email_verified = True
    profile.save(update_fields=["pending_email", "email_verified"])
