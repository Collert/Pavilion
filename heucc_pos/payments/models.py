from django.db import models
import uuid

# Create your models here.

class Transaction(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    amount = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    successful = models.BooleanField(default=False)