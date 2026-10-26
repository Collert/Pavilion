"""
The development cache (settings.CACHES without Redis): Django's file cache, made safe on Windows.

The file cache writes an entry by replacing its file. Windows won't open a file while another thread or process is
replacing it (or replace one that's open), so busy pages (the throttle counts every request) hit PermissionError.
Here each operation tries again for a moment; a read that still can't get in counts as a miss and a write that
can't is dropped, as a cache may. Production uses Redis.
"""
import logging
import os
import tempfile
import time

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.filebased import FileBasedCache

logger = logging.getLogger(__name__)

TRIES = 6


def _retry(method, fallback):
    def wrapper(self, *args, **kwargs):
        for attempt in range(TRIES):
            try:
                return method(self, *args, **kwargs)
            except PermissionError:
                time.sleep(0.002 * (attempt + 1))
        logger.debug("Cache file stayed locked: %s gave up", method.__name__)
        return fallback(*args, **kwargs) if callable(fallback) else fallback
    wrapper.__name__ = method.__name__
    return wrapper


class WindowsSafeFileCache(FileBasedCache):
    get = _retry(FileBasedCache.get, lambda key, default=None, version=None: default)

    def _set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        """Django's set, but swapping the file in with os.replace: its file_move_safe can't rename over a file on
        Windows and falls back to copying into it, which another thread can catch half done (or deleted)."""
        self._createdir()
        fname = self._key_to_file(key, version)
        self._cull()
        fd, tmp_path = tempfile.mkstemp(dir=self._dir)
        try:
            with open(fd, "wb") as f:
                self._write_content(f, timeout, value)
            os.replace(tmp_path, fname)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    set = _retry(_set, None)
    add = _retry(FileBasedCache.add, False)
    touch = _retry(FileBasedCache.touch, False)
    delete = _retry(FileBasedCache.delete, False)
    has_key = _retry(FileBasedCache.has_key, False)
