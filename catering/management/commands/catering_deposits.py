"""
Run once a day (cron): holds equipment deposits on the card when they're due (CateringSettings.hold_days before the
event; card holds only last about 7 days) and completes confirmed orders whose event is over with no deposit to settle.
"""
from django.core.management.base import BaseCommand

from catering.flow import daily


class Command(BaseCommand):
    help = "Hold catering deposits that are due and complete past orders"

    def handle(self, *args, **options):
        done = daily()
        self.stdout.write(f"Held {done['held']} deposits, completed {done['completed']} orders, {done['failed']} holds failed.")
