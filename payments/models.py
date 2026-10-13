from decimal import Decimal

from django.db import models
from django.utils.translation import gettext as _
import uuid

# Create your models here.

class PaymentError(Exception):
    """A payment that can't be used. The message is safe to show to the customer."""


class Transaction(models.Model):
    """
    One online card payment made through the Square Web Payments popup.

    Created (unpaid) when the popup opens. `successful` is only ever set by the server after Square
    accepts the charge (payments.views.process_web_payment). `used` is set once an order or gift card
    has been created from it, so the same payment can't be spent twice.
    """
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    successful = models.BooleanField(default=False)
    used = models.BooleanField(default=False)

    @classmethod
    def consume(cls, transaction_uuid, minimum_amount: Decimal):
        """
        Lock a paid, unused transaction covering at least `minimum_amount`, and mark it used.
        Must be called inside transaction.atomic(). Raises PaymentError otherwise.
        """
        try:
            transaction = cls.objects.select_for_update().get(uuid=transaction_uuid)
        except (cls.DoesNotExist, ValueError, TypeError, Exception):
            raise PaymentError(_("We couldn't find your card payment. Please try again."))
        if not transaction.successful:
            raise PaymentError(_("Your card payment didn't go through. Please try again."))
        if transaction.used:
            raise PaymentError(_("This payment has already been used."))
        if transaction.amount < minimum_amount:
            raise PaymentError(_("The amount paid doesn't cover the order total. Please try again."))
        transaction.used = True
        transaction.save(update_fields=["used"])
        return transaction

class PaymentAuthorization(models.Model):
    """
    A Square card payment tied to a Transaction (and through it, to an order).

    Online orders are only authorized when placed (status ""). The KDS captures the payment once every
    station approves the order, and voids it if the order is rejected (pos_server.views.order_marking).
    A payment that was captured right away has status "cap" and is refunded on rejection instead.
    """
    AUTHORIZED = ""
    CAPTURED = "cap"
    VOIDED = "del"
    statuses = (
        (AUTHORIZED, "Authorized"),
        (CAPTURED, "Captured"),
        (VOIDED, "Voided or refunded"),
    )
    payment_id = models.CharField(max_length=255, unique=True)
    amount = models.IntegerField(help_text="In cents")
    currency = models.CharField(max_length=3)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=3, choices=statuses, default=AUTHORIZED, blank=True)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="authorization")


class SquareTerminal(models.Model):
    """
    A Square Terminal paired with this POS through a device code (pos_server.views.pair_square_terminal).

    Pairing: create a code here, type it into the Terminal, then refresh. Once Square reports the code as
    PAIRED, `device_id` is filled in and this terminal becomes the one card payments are sent to.
    """
    name = models.CharField(max_length=100, default="Square Terminal")
    device_code_id = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20)
    status = models.CharField(max_length=20, default="UNPAIRED")
    pair_by = models.DateTimeField(null=True, blank=True)
    device_id = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.status})"

    @classmethod
    def current_device_id(cls):
        terminal = cls.objects.filter(is_active=True).exclude(device_id="").first()
        return terminal.device_id if terminal else None
