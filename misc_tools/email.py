"""
Email, sent through listmonk's transactional API (https://listmonk.app/docs/apis/transactional/).

Pavilion doesn't keep email wording itself: each kind of email (KINDS) is a listmonk "tx" template named
"Pavilion · <kind>". The first send creates it with a plain default, and the store edits it in listmonk from then on.
Data is available in the template as {{ .Tx.Data.<key> }}.

Settings (env): LISTMONK_URL, LISTMONK_API_USER, LISTMONK_API_TOKEN, and LISTMONK_FROM_EMAIL (optional; listmonk's
default sender otherwise). Without them nothing is sent: the email is logged, so local development works offline.
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

TIMEOUT = 15
NAME_PREFIX = "Pavilion · "


class EmailError(Exception):
    pass


def _wrap(body):
    return ('<div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#14261F">'
            f'{body}</div>')


# kind -> (subject, HTML body). Edited in listmonk after the first send; these are only the starting point.
KINDS = {
    "gift_card": (
        "Your gift card",
        _wrap('<h2>Here’s your gift card, {{ .Tx.Data.name }}</h2>'
              '<p>Show it at the counter or use it online.</p>'
              '<p><a href="{{ .Tx.Data.card_url }}">Open your gift card</a></p>'),
    ),
    "gift_card_gifted": (
        "{{ .Tx.Data.sender }} sent you a gift card",
        _wrap('<h2>{{ .Tx.Data.sender }} sent you a gift card</h2>'
              '<p>Hi {{ .Tx.Data.name }}, show it at the counter or use it online.</p>'
              '<p><a href="{{ .Tx.Data.card_url }}">Open your gift card</a></p>'),
    ),
    "receipt": (
        "Your receipt from {{ .Tx.Data.store }} · order #{{ .Tx.Data.number }}",
        _wrap('<h2>Thanks for your order</h2>'
              '<p>Order #{{ .Tx.Data.number }} · {{ .Tx.Data.when }}</p>'
              '<table style="width:100%;border-collapse:collapse">'
              '{{ range .Tx.Data.items }}<tr><td style="padding:4px 0">{{ .qty }}× {{ .name }}</td>'
              '<td style="text-align:right">{{ .total }}</td></tr>{{ end }}'
              '{{ range .Tx.Data.totals }}<tr><td style="padding:4px 0;color:#5B6A62">{{ .label }}</td>'
              '<td style="text-align:right">{{ .value }}</td></tr>{{ end }}</table>'
              '<p style="color:#5B6A62">{{ .Tx.Data.store }}</p>'),
    ),
}


def is_configured():
    return bool(settings.LISTMONK_URL and settings.LISTMONK_API_USER and settings.LISTMONK_API_TOKEN)


def api(method, path, payload=None):
    """Call the listmonk API and return the response's `data`."""
    request = urllib.request.Request(
        f"{settings.LISTMONK_URL.rstrip('/')}/api{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"token {settings.LISTMONK_API_USER}:{settings.LISTMONK_API_TOKEN}",
                 "Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "Pavilion/1.0 (listmonk)"},  # Cloudflare rejects urllib's default agent
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except (ValueError, AttributeError):
            pass
        raise EmailError(f"listmonk returned {exc.code}: {detail}"[:300]) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise EmailError(f"listmonk unreachable: {getattr(exc, 'reason', exc)}") from exc
    return json.loads(body).get("data") if body else None


def template_id(kind):
    """The listmonk template for `kind`, created from KINDS if it's missing. Cached for an hour."""
    key = f"listmonk-template:{kind}"
    cached = cache.get(key)
    if cached:
        return cached
    name = NAME_PREFIX + kind
    rows = api("GET", "/templates?no_body=true")
    rows = rows.get("results", []) if isinstance(rows, dict) else (rows or [])
    found = next((row["id"] for row in rows if row.get("name") == name and row.get("type") == "tx"), None)
    if not found:
        subject, body = KINDS[kind]
        found = api("POST", "/templates", {"name": name, "type": "tx", "subject": subject, "body": body})["id"]
    cache.set(key, found, 60 * 60)
    return found


def send(kind, to, data):
    """
    Send one email of `kind` to the address `to`. Returns True when listmonk accepted it, False when email isn't
    set up (it's logged instead). Raises EmailError when listmonk refuses or can't be reached.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown email kind: {kind}")
    if not is_configured():
        logger.info("Email not sent (listmonk isn't configured): %s to %s with %s", kind, to, data)
        return False
    payload = {"subscriber_emails": [to], "subscriber_mode": "fallback",  # also to addresses that aren't subscribers
               "template_id": template_id(kind), "data": data, "content_type": "html"}
    if settings.LISTMONK_FROM_EMAIL:
        payload["from_email"] = settings.LISTMONK_FROM_EMAIL
    api("POST", "/tx", payload)
    return True
