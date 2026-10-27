"""
Reading the store's settings while Pavilion starts (AdminPortalConfig.ready: languages, time zone).

Under uvicorn, Django starts inside the event loop (the ASGI app is imported there), and Django refuses database
calls from async code (SynchronousOnlyOperation). So the read runs on a short-lived thread of its own, with its own
connection, closed when it's done. Before `migrate` (no table yet) or without a database it quietly gives None.
"""
import threading
import warnings


def store_row(*fields):
    """{field: value} of the StoreSettings row, or None (no row, no table, no database)."""
    result = {}

    def read():
        from django.db import DatabaseError, connection

        from .models import StoreSettings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # "accessing the database during app initialization"
                result["row"] = StoreSettings.objects.filter(pk=1).values(*fields).first()
        except DatabaseError:
            pass
        finally:
            connection.close()

    worker = threading.Thread(target=read, name="pavilion-startup")
    worker.start()
    worker.join()
    return result.get("row")
