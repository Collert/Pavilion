"""
Run once a day (cron): deletes gift cards that have been empty for 30 days (gift_cards/giftcards.py: cleanup).
"""
from django.core.management.base import BaseCommand

from gift_cards.giftcards import cleanup


class Command(BaseCommand):
    help = "Delete gift cards that have been empty for 30 days"

    def handle(self, *args, **options):
        self.stdout.write(f"Deleted {cleanup()} empty gift cards.")
