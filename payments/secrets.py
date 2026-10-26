"""Encrypting what Pavilion keeps of other services' credentials (the Square OAuth tokens), with a key derived from
SECRET_KEY. Changing SECRET_KEY makes them unreadable: connect Square again."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key = hashlib.sha256(f"pavilion-secrets:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode() if text else ""


def decrypt(token: str) -> str:
    """The text, or "" if it can't be read (e.g. SECRET_KEY changed)."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
