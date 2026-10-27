"""Show the first-time setup code (users/setup.py), for whoever runs the server: docker compose exec web python manage.py setup_code"""
from django.core.management.base import BaseCommand

from users import setup


class Command(BaseCommand):
    help = "Show the code that finishes Pavilion's first-time setup (while it has no account yet)."

    def handle(self, *args, **options):
        if not setup.needed():
            self.stdout.write("Pavilion is already set up: sign in instead.")
            return
        self.stdout.write(f"Setup code: {setup.setup_code()}")
