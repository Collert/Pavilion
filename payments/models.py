from django.db import models
import uuid

# Create your models here.

class Transaction(models.Model):
    """
    Transaction model to store payment transaction details.

    Attributes:
        uuid (UUIDField): Unique identifier for the transaction, automatically generated.
        amount (DecimalField): The amount of the transaction, with a maximum of 5 digits and 2 decimal places.
        successful (BooleanField): Indicates whether the transaction was successful or not.
    """
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    amount = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    successful = models.BooleanField(default=False)

class PaymentAuthorization(models.Model):
    """
    PaymentAuthorization model represents the authorization details of a payment.

    Attributes:
        statuses (tuple): Choices for the status of the payment authorization.
        payment_id (CharField): Unique identifier for the payment.
        amount (IntegerField): Amount of the payment.
        currency (CharField): Currency code of the payment (ISO 4217 format).
        created_at (DateTimeField): Timestamp when the payment authorization was created.
        status (CharField): Status of the payment authorization, chosen from `statuses`.
        transaction (ForeignKey): Reference to the associated Transaction object.
    """
    statuses = (
        ("cap", "Captured"),
        ("del", "Deleted")
    )
    payment_id = models.CharField(max_length=255, unique=True)
    amount = models.IntegerField()
    currency = models.CharField(max_length=3)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=3, choices=statuses)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="authorization")