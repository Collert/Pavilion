import unittest
import warnings

import django.utils.deprecation
from django.test.runner import DiscoverRunner


class StrictDeprecationRunner(DiscoverRunner):
    """Fail tests that touch anything Django has deprecated (RemovedInDjango…Warning), so upgrades stay easy."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        for name in dir(django.utils.deprecation):
            if name.startswith("Removed"):
                warnings.filterwarnings("error", category=getattr(django.utils.deprecation, name))
        from django.conf import settings
        settings.THROTTLE = False  # tests fire requests faster than anyone could tap; ThrottleTests turn it on
        settings.SETUP_REDIRECT = False  # many tests open pages before making an account; SetupTests turn it on
        import audio.calls
        audio.calls.background = False  # spoken calls run straight away in tests, not in threads

    def get_resultclass(self):
        """Each test starts without the cached StoreSettings row: the cache outlives a test's rolled-back database."""
        base = super().get_resultclass() or unittest.TextTestResult

        class Result(base):
            def startTest(self, test):
                from admin_portal.models import StoreSettings
                StoreSettings.forget()
                from pos_server import estimates
                estimates.forget()
                super().startTest(test)
        return Result
