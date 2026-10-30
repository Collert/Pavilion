"""
Sending push notifications, after the transaction commits and in the background (a push service can take a while to
answer, and one slow answer mustn't hold up the register or the kitchen).

A notification is a dict the service workers show (push/templates/push/sw.js): title, body, url (opened on a tap),
tag (a newer one with the same tag replaces it), icon, badge. Each subscription gets it in its own language.

Dead subscriptions don't stay: a push service answering 404 or 410 (the browser unsubscribed, or notifications were
turned off) deletes the row straight away; other refusals (400, 401, 403, 413) count up and three in a row delete it.
Busy or unreachable services (429, 5xx, timeouts) are tried again with the next notification.
"""
import json
import logging
import threading

from django.db import close_old_connections, transaction
from django.utils import timezone, translation
from pywebpush import WebPushException, webpush

from . import vapid

logger = logging.getLogger(__name__)
GONE = {404, 410}
REFUSED = {400, 401, 403, 413}
GIVE_UP = 3


def _contact() -> str:
    from admin_portal import store_info
    return f"mailto:{store_info.contact_email()}"


def background(job):
    """Run `job` on its own thread with its own database connection (tests run it straight away)."""
    def run():
        close_old_connections()
        try:
            job()
        except Exception:
            logger.exception("Sending push notifications failed")
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()


_pool = None


def pooled(job):
    """Run `job` on a small shared pool (listmonk posts a campaign's messages in bursts: a thread each would pile up)."""
    global _pool
    from concurrent.futures import ThreadPoolExecutor
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pavilion-push")

    def run():
        close_old_connections()
        try:
            job()
        except Exception:
            logger.exception("Sending push notifications failed")
        finally:
            close_old_connections()

    _pool.submit(run)


def deliver(sub, message: dict, *, signer=None, urgency="normal", ttl=24 * 3600) -> bool:
    """Send one notification now. True when the push service took it. Drops subscriptions that are gone."""
    from .models import PushSubscription
    try:
        webpush(sub.info(), json.dumps(message), vapid_private_key=signer or vapid.signer(),
                vapid_claims={"sub": _contact()}, ttl=ttl, timeout=10, headers={"Urgency": urgency})
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in GONE:
            PushSubscription.objects.filter(pk=sub.pk).delete()
        elif status in REFUSED:
            sub.failures += 1
            if sub.failures >= GIVE_UP:
                PushSubscription.objects.filter(pk=sub.pk).delete()
            else:
                PushSubscription.objects.filter(pk=sub.pk).update(failures=sub.failures)
            logger.warning("Push service refused %s (%s): %s", sub.pk, status, getattr(e.response, "text", "")[:200])
        else:
            logger.warning("Push to %s didn't go through: %s", sub.pk, e)
        return False
    except Exception as e:  # network trouble: try again next time
        logger.warning("Push to %s didn't go through: %s", sub.pk, e)
        return False
    PushSubscription.objects.filter(pk=sub.pk).update(failures=0, last_success_at=timezone.now())
    return True


def send_now(subscriptions, build, **options) -> int:
    """Send `build()` (called in each subscription's language, once per language) to each one. Returns how many went."""
    from django.conf import settings
    subs = list(subscriptions)
    if not subs:
        return 0
    signer = vapid.signer()
    messages, sent = {}, 0
    for sub in subs:
        lang = sub.language or settings.LANGUAGE_CODE
        if lang not in messages:
            with translation.override(lang):
                messages[lang] = {k: str(v) for k, v in build().items() if v is not None}
        sent += deliver(sub, messages[lang], signer=signer, **options)
    return sent


def notify(subscriptions, build, **options):
    """After this transaction, in the background: send_now. `subscriptions` is read now (a queryset is evaluated)."""
    subs = list(subscriptions)
    if subs:
        transaction.on_commit(lambda: background(lambda: send_now(subs, build, **options)))
