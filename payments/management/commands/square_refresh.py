"""Daily cron: renew the connected Square account's access token before it runs out (they last 30 days)."""
from django.core.management.base import BaseCommand

from payments import square_oauth
from payments.models import SquareConnection


class Command(BaseCommand):
    help = "Renew the Square access token when it has less than a week left (run daily)."

    def handle(self, *args, **options):
        connection = SquareConnection.current()
        if connection is None:
            self.stdout.write("Square isn't connected.")
            return
        square_oauth.access_token(connection)  # refreshes when it's due
        connection.refresh_from_db()
        self.stdout.write(f"Square access is good until {connection.expires_at:%Y-%m-%d}.")
