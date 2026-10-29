"""
Languages (design: AdminLanguages): which of Pavilion's languages (settings.SUPPORTED_LANGUAGES) the store uses, its
default and whether people start in their device's language. Saved on StoreSettings and applied when Pavilion starts
(`load`, from AdminPortalConfig.ready): settings.LANGUAGES, LANGUAGE_CODE and LANGUAGE_MATCH_DEVICE are set for the
process, so URLs, the language picker and translations follow. Changing them needs a restart (`restart`): Pavilion
under uvicorn's worker supervisor reloads its workers on SIGHUP (one at a time; the Dockerfile caps how long an old
worker waits for its open SSE streams, else it waits forever and the restart never finishes), the development server
reloads when this file is touched; anywhere else the page says to restart it by hand. The restart is noted in the
cache (`restarting`), so every worker that hasn't restarted yet shows it as under way; with several workers, the page
waits until the one answering is running the saved settings.

    request.session["admin_languages"] = {"on": [codes], "default", "auto", "q", "filter": "all"|"on"}
"""
import functools
import multiprocessing
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _, to_locale

KEY = "admin_languages"
STARTED = time.time()   # when this process started: a restart asked for after it hasn't reached it yet
RESTART_KEY = "admin:restart"
RUNNING = {"on": [], "default": "", "auto": True}


# ---------- what's running ----------

def catalog() -> list:
    """[(code, English name)] Pavilion has translations for."""
    return list(settings.SUPPORTED_LANGUAGES)


def resolve(codes, default):
    """(the enabled codes, default first; the default): only known codes, none means all of them."""
    known = [c for c, _name in catalog()]
    codes = [c for c in known if c in (codes or [])] or known
    default = default if default in codes else codes[0]
    return [default] + [c for c in codes if c != default], default


def apply(codes, default, auto=True):
    """Set this process's languages (at startup, before any request)."""
    codes, default = resolve(codes, default)
    names = dict(catalog())
    settings.LANGUAGES = [(c, names[c]) for c in codes]
    settings.LANGUAGE_CODE = default
    settings.LANGUAGE_MATCH_DEVICE = bool(auto)
    from django.utils.translation import trans_real
    trans_real.reset_cache(setting="LANGUAGES")  # what Django does when the setting changes: forget the cached lookups
    RUNNING.update(on=codes, default=default, auto=bool(auto))


def load():
    """Apply the saved languages. Skipped by tests, and quietly when the table isn't there yet (before migrate)."""
    if "test" in sys.argv[1:2]:
        RUNNING.update(on=[c for c, _n in settings.LANGUAGES], default=settings.LANGUAGE_CODE, auto=True)
        return
    from .startup import store_row
    row = store_row("languages", "default_language", "match_device")  # on its own thread: uvicorn starts in async code
    if row:
        apply(row["languages"], row["default_language"], row["match_device"])
    else:
        apply([], settings.LANGUAGE_CODE, True)


def saved(store) -> dict:
    codes, default = resolve(store.languages, store.default_language)
    return {"on": codes, "default": default, "auto": store.match_device}


def restart_pending(store) -> bool:
    now = saved(store)
    return sorted(now["on"]) != sorted(RUNNING["on"]) or now["default"] != RUNNING["default"] or now["auto"] != RUNNING["auto"]


def restart_method():
    """"uvicorn" (a worker under uvicorn's supervisor), "runserver" (the reloading dev server) or None."""
    if "uvicorn" in sys.modules and hasattr(signal, "SIGHUP") and multiprocessing.parent_process() is not None:
        return "uvicorn"
    if os.environ.get("RUN_MAIN") == "true":
        return "runserver"
    return None


def restart() -> bool:
    """Restart Pavilion a second from now (so this answer gets out). False when it can't restart itself."""
    method = restart_method()
    if method is None:
        return False

    def go():
        if method == "uvicorn":
            os.kill(os.getppid(), signal.SIGHUP)
        else:
            os.utime(__file__)

    cache.set(RESTART_KEY, time.time(), 120)
    threading.Timer(1.0, go).start()
    return True


def restarting() -> bool:
    """A restart was asked for in the last two minutes and this process is older than it."""
    at = cache.get(RESTART_KEY)
    return at is not None and at > STARTED


# ---------- translations ----------

@functools.lru_cache(maxsize=None)
def coverage(code) -> int:
    """How much of Pavilion's text is translated into `code`, in percent (from its .po file)."""
    if code.split("-")[0] == "en":
        return 100
    total = done = 0
    for base in settings.LOCALE_PATHS:
        po = Path(base) / to_locale(code) / "LC_MESSAGES" / "django.po"
        if not po.exists():
            continue
        for entry in po.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n\n"):  # files saved on Windows too
            lines = [line for line in entry.splitlines() if not line.startswith("#~")]  # obsolete entries don't count
            fields, current = {}, None
            for line in lines:
                match = re.match(r'^(msgid|msgid_plural|msgstr(?:\[\d+\])?) "(.*)"$', line)
                if match:
                    current = match.group(1)
                    fields[current] = match.group(2)
                elif current and line.startswith('"'):
                    fields[current] += line.strip('"')
            if "msgid" not in fields or not fields["msgid"]:
                continue  # the header, or not an entry
            total += 1
            if any(v for k, v in fields.items() if k.startswith("msgstr")) and "#, fuzzy" not in entry:
                done += 1
    return round(done * 100 / total) if total else 0


def native(code) -> str:
    from django.utils.translation import get_language_info
    try:
        return get_language_info(code)["name_local"]
    except KeyError:
        return code


# ---------- the page ----------

def state(session, store) -> dict:
    st = session.get(KEY)
    if not st:
        st = {**saved(store), "q": "", "filter": "all"}
    return st


def take_post(st, post):
    if "q" in post:
        st["q"] = post.get("q", "").strip()[:40]
    if post.get("default") in st["on"]:
        st["default"] = post["default"]


def apply_op(st, op):
    name, _sep, arg = op.partition(":")
    known = [c for c, _n in catalog()]
    if name == "toggle" and arg in known and arg != st["default"]:
        st["on"] = [c for c in known if c in (set(st["on"]) ^ {arg})]
    elif name == "auto":
        st["auto"] = not st["auto"]
    elif name == "filter" and arg in ("all", "on"):
        st["filter"] = arg


def save(st, store):
    codes, default = resolve(st["on"], st["default"])
    store.languages = codes
    store.default_language = default
    store.match_device = st["auto"]
    store.save(update_fields=["languages", "default_language", "match_device"])


def page(st, store) -> dict:
    current = saved(store)
    draft = {"on": resolve(st["on"], st["default"])[0], "default": st["default"], "auto": st["auto"]}
    dirty = sorted(draft["on"]) != sorted(current["on"]) or draft["default"] != current["default"] or draft["auto"] != current["auto"]
    default_name = dict(catalog()).get(st["default"], "")
    q = st["q"].lower()
    rows = []
    for code, english in catalog():
        on = code in draft["on"]
        if st["filter"] == "on" and not on:
            continue
        if q and q not in f"{native(code)} {english} {code}".lower():
            continue
        pct = coverage(code)
        rows.append({"code": code, "tag": code.split("-")[0].upper(), "native": native(code), "english": str(english), "on": on,
                     "default": code == st["default"], "pct": pct, "level": "full" if pct == 100 else "partial" if pct < 90 else "near",
                     "cov": (_("Fully translated") if pct == 100 else _("Partial · gaps in %(lang)s") % {"lang": default_name} if pct < 90
                             else _("Nearly complete"))})
    on_list = [c for c in draft["on"]]
    return {"st": st, "rows": rows, "dirty": dirty, "count": len(on_list), "total": len(catalog()),
            "defaults": [(c, native(c), dict(catalog())[c]) for c in on_list],
            "preview": [(c, native(c), coverage(c) < 90) for c in on_list],
            "pending": restart_pending(store), "can_restart": restart_method() is not None,
            "running": [native(c) for c in RUNNING["on"]]}
