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
    card_brand = models.CharField(max_length=24, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)


class SquareTerminal(models.Model):
    """
    A Square Terminal paired with one register (payments/square.py: pair_terminal). The register asks Square for a
    device code; once it's typed into the Terminal, Square's device.code.paired webhook (or "Check" on the register)
    fills in `device_id` and that register's card payments go to it. Each register has its own; a register without
    one takes cards on the customer's phone (a QR code).
    """
    name = models.CharField(max_length=100, default="Square Terminal")
    register = models.ForeignKey("pos_server.EligibleDevice", null=True, blank=True, on_delete=models.SET_NULL, related_name="terminals",
                                 help_text="The register that sends its card payments here")
    device_code_id = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20)
    status = models.CharField(max_length=20, default="UNPAIRED", help_text="Square's device code status: UNPAIRED, PAIRED, EXPIRED")
    pair_by = models.DateTimeField(null=True, blank=True)
    device_id = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.status})"

    @property
    def expired(self) -> bool:
        """A code nobody typed in time (Square gives 5 minutes); a new one has to be made."""
        from django.utils import timezone
        return self.status == "EXPIRED" or (self.status == "UNPAIRED" and self.pair_by is not None and self.pair_by < timezone.now())

    @classmethod
    def for_register(cls, register):
        """The paired Terminal of a register (EligibleDevice or None), or None."""
        return cls.objects.filter(register=register, status="PAIRED").exclude(device_id="").first() if register else None


class TerminalCheckout(models.Model):
    """
    One card payment a register asked for (payments/square.py). On a Terminal (start_checkout): Square's
    terminal.checkout.updated webhook keeps `status` current. On the customer's phone (start_phone_checkout, for
    registers without a Terminal): the register shows a QR code for /pay/<link>, where the customer pays by card
    (payments/views.py: phone_pay). Either way the register that started it waits on the "checkout" nudge and asks for
    its own checkout only, and placing the order claims it (`order`), once.
    """
    PENDING, IN_PROGRESS, CANCEL_REQUESTED, CANCELED, COMPLETED = "PENDING", "IN_PROGRESS", "CANCEL_REQUESTED", "CANCELED", "COMPLETED"
    FINISHED = (CANCELED, COMPLETED)
    TERMINAL, PHONE = "terminal", "phone"
    checkout_id = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=8, choices=[(TERMINAL, "Square Terminal"), (PHONE, "Customer's phone")], default=TERMINAL)
    link = models.CharField(max_length=40, blank=True, db_index=True, help_text="Phone payments: the /pay/<link> address")
    summary = models.JSONField(default=list, blank=True, help_text="Phone payments: what's being paid for [{qty, title, total}]")
    register = models.ForeignKey("pos_server.EligibleDevice", null=True, blank=True, on_delete=models.SET_NULL, related_name="checkouts")
    terminal = models.ForeignKey(SquareTerminal, null=True, blank=True, on_delete=models.SET_NULL, related_name="checkouts")
    device_id = models.CharField(max_length=100, blank=True, help_text="Square's id of the Terminal it went to")
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=20, default=PENDING)
    payment_ids = models.JSONField(default=list, blank=True)
    cancel_reason = models.CharField(max_length=40, blank=True)
    started_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    order = models.ForeignKey("pos_server.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="terminal_checkouts",
                              help_text="The order that used it")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.checkout_id} ${self.amount} {self.status}"

    @property
    def finished(self) -> bool:
        return self.status in self.FINISHED


class SquareConnection(models.Model):
    """
    The store's own Square account, connected on the admin's Payments page through OAuth (payments/square_oauth.py).
    One row. Its access token (30 days, refreshed before it runs out) and refresh token are kept encrypted
    (payments/secrets.py). Without a row, settings.SQUARE_ACCESS_TOKEN / SQUARE_LOCATION_ID are used (development).
    """
    merchant_id = models.CharField(max_length=64)
    business_name = models.CharField(max_length=200, blank=True)
    environment = models.CharField(max_length=12, default="sandbox", help_text="sandbox or production, as it was connected")
    access_token = models.TextField(help_text="Encrypted")
    refresh_token = models.TextField(blank=True, help_text="Encrypted")
    expires_at = models.DateTimeField(null=True, blank=True)
    location_id = models.CharField(max_length=64, blank=True, help_text="The Square location payments go to")
    location_name = models.CharField(max_length=200, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    connected_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    refreshed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.business_name or self.merchant_id} ({self.environment})"

    @classmethod
    def current(cls):
        return cls.objects.order_by("-connected_at").first()
