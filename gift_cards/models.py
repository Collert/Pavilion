from django.db import models
from decimal import Decimal
from django.utils.crypto import get_random_string

# Create your models here.

class GiftCard(models.Model):
    email = models.CharField(max_length=64, blank=True)
    image = models.ImageField(upload_to='files/gift-cards/presets', null=True)
    available_balance = models.DecimalField(null = False, default=0, max_digits=5, decimal_places=2)
    number = models.CharField(
        max_length=16,
        default="",
        unique=True,
        null=False
    )

    def save(self, *args, **kwargs):
        # Generate a unique `number` if it's not set or if it clashes with an existing one
        if not self.number:
            self.number = self.generate_unique_number()
        super().save(*args, **kwargs)

    @classmethod
    def generate_unique_number(cls):
        while True:
            new_number = get_random_string(length=16, allowed_chars='1234567890')
            if not cls.objects.filter(number=new_number).exists():
                return new_number
    
    def charge_card(self, amount:Decimal) -> int:
        if self.available_balance >= amount:
            self.available_balance -= amount
            self.save()
            return 200
        else:
            return 402

    def load_card(self, amount:Decimal) -> None:
        self.available_balance += amount
        self.save()

class GiftCardAuthorization(models.Model):
    card = models.ForeignKey(GiftCard, on_delete = models.CASCADE)
    order = models.ForeignKey("pos_server.Order", on_delete=models.CASCADE, related_name="gift_card_auth")
    charged_balance = models.DecimalField(decimal_places=2, max_digits=5)

class CardPreset(models.Model):
    name = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(null = False, max_digits=5, decimal_places=2)
    image = models.ImageField(upload_to='files/gift-cards/presets', null=True)

    def __str__(self) -> str:
        return self.name
    
class SpecialCard(CardPreset):
    cost = models.DecimalField(null = False, max_digits=5, decimal_places=2)

from django.db import transaction

def clean_duplicates():
    # Dictionary to track unique numbers
    unique_numbers = set()

    # Begin a transaction
    with transaction.atomic():
        for obj in GiftCard.objects.all():
            if obj.number in unique_numbers:
                # Delete duplicate if the number is already in the set
                obj.delete()
                print(f"Deleted duplicate with number: {obj.number}")
            else:
                # Otherwise, add the number to the set
                unique_numbers.add(obj.number)

    print("Duplicate cleanup complete.")
