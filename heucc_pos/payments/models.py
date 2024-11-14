from django.db import models
import uuid

# Create your models here.

class Transaction(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    amount = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    successful = models.BooleanField(default=False)

class PaymentAuthorization(models.Model):
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