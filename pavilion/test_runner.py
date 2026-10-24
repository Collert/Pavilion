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
        import audio.calls
        audio.calls.background = False  # spoken calls run straight away in tests, not in threads
