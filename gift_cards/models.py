from django.db import models
from decimal import Decimal
from django.utils.crypto import get_random_string

# Create your models here.

class GiftCard(models.Model):
    """
    GiftCard model represents a gift card with an associated email, image, available balance, and unique number.
    Attributes:
        email (CharField): The email associated with the gift card. Optional.
        image (ImageField): The image associated with the gift card. Optional.
        available_balance (DecimalField): The available balance on the gift card. Required.
        number (CharField): The unique number of the gift card. Required.
    Methods:
        save(*args, **kwargs): Saves the gift card instance, generating a unique number if not set.
        generate_unique_number(): Generates a unique 16-digit number for the gift card.
        charge_card(amount: Decimal) -> int: Charges the gift card with the specified amount if the balance is sufficient. Returns 200 on success, 402 on insufficient balance.
        load_card(amount: Decimal) -> None: Loads the gift card with the specified amount.
    """
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
        """
        Generates a unique 16-digit number for a gift card.

        This method continuously generates a random 16-digit number using the 
        specified allowed characters ('1234567890') until it finds one that 
        does not already exist in the database.

        Returns:
            str: A unique 16-digit number.
        """
        while True:
            new_number = get_random_string(length=16, allowed_chars='1234567890')
            if not cls.objects.filter(number=new_number).exists():
                return new_number
    
    def charge_card(self, amount:Decimal) -> int:
        """
        Charges the gift card with the specified amount if the available balance is sufficient.

        Args:
            amount (Decimal): The amount to charge to the gift card.

        Returns:
            int: 200 if the charge was successful, 402 if the available balance is insufficient.
        """
        if self.available_balance >= amount:
            self.available_balance -= amount
            self.save()
            return 200
        else:
            return 402

    def load_card(self, amount:Decimal) -> None:
        """
        Loads a specified amount onto the gift card and updates the available balance.

        Args:
            amount (Decimal): The amount to be loaded onto the gift card.

        Returns:
            None
        """
        self.available_balance += amount
        self.save()

class GiftCardAuthorization(models.Model):
    """
    Represents an authorization for a gift card used in an order.

    Attributes:
        card (ForeignKey): The gift card being authorized.
        order (ForeignKey): The order associated with the gift card authorization.
        charged_balance (DecimalField): The amount charged to the gift card.
    """
    card = models.ForeignKey(GiftCard, on_delete = models.CASCADE)
    order = models.ForeignKey("pos_server.Order", on_delete=models.CASCADE, related_name="gift_card_auth")
    charged_balance = models.DecimalField(decimal_places=2, max_digits=5)

class CardPreset(models.Model):
    """
    CardPreset model represents a preset configuration for gift cards.
    Attributes:
        name (str): The name of the card preset. It is an optional field with a maximum length of 64 characters.
        amount (Decimal): The amount associated with the card preset. It is a required field with a maximum of 5 digits and 2 decimal places.
        image (ImageField): An optional image associated with the card preset. The image is uploaded to 'files/gift-cards/presets'.
    Methods:
        __str__(): Returns the name of the card preset as its string representation.
    """
    name = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(null = False, max_digits=5, decimal_places=2)
    image = models.ImageField(upload_to='files/gift-cards/presets', null=True)

    def __str__(self) -> str:
        return self.name
    
class SpecialCard(CardPreset):
    cost = models.DecimalField(null = False, max_digits=5, decimal_places=2)

from django.db import transaction

def clean_duplicates():
    """
    Removes duplicate GiftCard objects based on their 'number' attribute.
    This function iterates through all GiftCard objects and deletes any
    duplicates, ensuring that only one GiftCard object with a unique 'number'
    remains. The operation is performed within a database transaction to
    ensure atomicity.
    Steps:
    1. Initializes a set to track unique numbers.
    2. Begins a database transaction.
    3. Iterates through all GiftCard objects.
    4. Deletes any GiftCard object whose 'number' is already in the set.
    5. Adds the 'number' of non-duplicate GiftCard objects to the set.
    6. Prints a message for each deleted duplicate.
    7. Prints a completion message after all duplicates are removed.
    Note:
    - This function assumes that the GiftCard model has a 'number' attribute.
    - The transaction ensures that the database remains consistent in case of
      an error during the operation.
    """
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
