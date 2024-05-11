from django.core.management.base import BaseCommand
from payments.models import Transaction

class Command(BaseCommand):
    help = 'Deletes all Transaction objects'

    def handle(self, *args, **options):
        all = Transaction.objects.all()
        for one in all:
            one.delete()
