"""makemessages that also finds the JavaScript strings wrapped in our `t()` helper.

Many scripts define `const t = (text) => gettext(text);` and call `t("…")`; xgettext only knows `gettext`, so
those strings never reached djangojs.po. Run as usual: `manage.py makemessages -d djangojs -l uk`.
"""
from django.core.management.commands import makemessages


class Command(makemessages.Command):
    def handle(self, *args, **options):
        if options.get("domain") == "djangojs":
            self.xgettext_options = self.xgettext_options + ["--keyword=t"]
        return super().handle(*args, **options)
