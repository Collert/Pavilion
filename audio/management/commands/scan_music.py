from django.core.management.base import BaseCommand

from audio.library import scan


class Command(BaseCommand):
    help = "Add the songs in files/music/ (the media storage) to the library, and drop songs whose files are gone."

    def handle(self, *args, **options):
        result = scan()
        self.stdout.write(f"Added {result['added']}, removed {result['removed']}; {result['total']} songs in the library.")
