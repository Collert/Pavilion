"""
Square: Terminal payments for the registers (each register pairs its own Terminal), Web Payments for the online
store, captures, voids, refunds and saved cards.

Terminal flow: a register pairs a Terminal with a device code (pair_terminal; Square's device.code.paired webhook,
or refresh_device_code, finishes it). A card payment starts a TerminalCheckout on that register's Terminal
(start_checkout); Square's terminal.checkout.updated webhook updates its row (checkout_event) and publishes
"checkout", and the register asks for its own checkout. Nothing is kept in process memory or under global cache
keys, so any number of registers and workers can take card payments at once. Placing the order claims the
checkout (claim_checkout): this register's, completed, for that amount, used once.
"""
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _, gettext_lazy
from pavilion.realtime import publish

from .models import PaymentAuthorization, SquareConnection, SquareTerminal, TerminalCheckout

logger = logging.getLogger(__name__)


class _SquareClient:
    """Square's API for the store's own account: the one connected on the admin's Payments page (OAuth, refreshed
    as needed; payments/square_oauth.py), else SQUARE_ACCESS_TOKEN from the environment (development). Built for
    each use, so a new connection takes effect at once in every worker."""

    def __getattr__(self, name):
        from .square_oauth import access_token, seller_client
        connection = SquareConnection.current()
        token = access_token(connection) if connection else (settings.SQUARE_ACCESS_TOKEN or "")
        return getattr(seller_client(token), name)


square_client = _SquareClient()


def location_id() -> str:
    """The Square location payments go to: the connected account's pick, else SQUARE_LOCATION_ID."""
    connection = SquareConnection.current()
    return (connection.location_id if connection else "") or settings.SQUARE_LOCATION_ID or ""


def connected() -> bool:
    """Whether Square can be used: an account is connected, or a token is set in the environment."""
    return bool(SquareConnection.current() or settings.SQUARE_ACCESS_TOKEN)


# ---------- pairing a Terminal with a register ----------

def pair_terminal(register, name=""):
    """Ask Square for a device code for this register's Terminal (typed in on the Terminal: "Use a device code").
    Codes it asked for before and never used are dropped. Raises SquareError."""
    name = (name or (register.name if register else "") or "Square Terminal")[:100]
    response = square_client.devices.create_device_code({
        "idempotency_key": str(uuid.uuid4()),
        "device_code": {"name": name, "product_type": "TERMINAL_API", "location_id": location_id()},
    })
    if not response.is_success():
        raise SquareError(response.errors)
    SquareTerminal.objects.filter(register=register).exclude(status="PAIRED").delete()
    code = response.body["device_code"]
    return SquareTerminal.objects.create(
        name=name, register=register, device_code_id=code["id"], code=code["code"], status=code.get("status", "UNPAIRED"),
        pair_by=parse_datetime(code["pair_by"]) if code.get("pair_by") else None)


def _device_code_changed(terminal, code):
    terminal.status = code.get("status", terminal.status)
    if terminal.status == "PAIRED" and code.get("device_id"):
        terminal.device_id = code["device_id"]
        if terminal.register_id:  # one Terminal per register: the newly paired one replaces the old
            SquareTerminal.objects.filter(register_id=terminal.register_id).exclude(pk=terminal.pk).update(register=None)
        # and one register per Terminal: a Terminal paired again (a new code) leaves the register it was with
        SquareTerminal.objects.filter(device_id=terminal.device_id).exclude(pk=terminal.pk).update(register=None)
    terminal.save()
    publish("checkout")
    return terminal


def refresh_device_code(terminal):
    """Ask Square how a pairing code is doing ("Check" on the register, when no webhook reached us)."""
    response = square_client.devices.get_device_code(terminal.device_code_id)
    if not response.is_success():
        raise SquareError(response.errors)
    return _device_code_changed(terminal, response.body["device_code"])


def device_code_event(code: dict):
    """Square's device.code.paired webhook: the code was typed into a Terminal."""
    terminal = SquareTerminal.objects.filter(device_code_id=code.get("id", "")).first()
    return _device_code_changed(terminal, code) if terminal else None


# Square's sandbox has no Terminals to pair: these test device ids each act out one outcome
# (https://developer.squareup.com/docs/devtools/sandbox/testing). Approvals go through up to $25; more stays pending.
SANDBOX_TERMINALS = [
    ("9fa747a2-25ff-48ee-b078-04381f7c828f", gettext_lazy("Card approved")),
    ("22cd266c-6246-4c06-9983-67f0c26346b0", gettext_lazy("Card approved, with a 20% tip")),
    ("388b5a08-a77c-48ef-ad2a-4a790e6f2789", gettext_lazy("Interac approved (CAD)")),
    ("841100b9-ee60-4537-9bcf-e30b2ba5e215", gettext_lazy("Buyer cancels")),
    ("0a956d49-619a-4530-8e5e-8eac603ffc5e", gettext_lazy("Times out")),
    ("da40d603-c2ea-4a65-8cfd-f42e36dab0c7", gettext_lazy("Terminal offline")),
]


def sandbox() -> bool:
    from .square_oauth import environment
    return environment() == "sandbox"


def use_sandbox_terminal(register, device_id):
    """Sandbox only: give the register one of Square's test Terminals instead of pairing a real one."""
    labels = dict(SANDBOX_TERMINALS)
    if not sandbox() or device_id not in labels:
        raise SquareError(_("Test Terminals are only for Square's sandbox."))
    SquareTerminal.objects.filter(register=register).update(register=None)
    terminal, _created = SquareTerminal.objects.update_or_create(
        device_code_id=f"sandbox:{register.id}:{device_id}",
        defaults={"name": _("Test Terminal: %(what)s") % {"what": labels[device_id]}, "register": register, "code": "",
                  "status": "PAIRED", "device_id": device_id})
    publish("checkout")
    return terminal


def unpair_terminal(register):
    """This register stops using its Terminal (Square still knows the Terminal; pair it again to use it)."""
    SquareTerminal.objects.filter(register=register).update(register=None)
    publish("checkout")


# ---------- card payments on a Terminal ----------

def gather_terminal_checkout(amount: Decimal, device_id: str, reference: str = ""):
    """Ask Square to take `amount` on the Terminal `device_id`. Returns Square's response."""
    return square_client.terminal.create_terminal_checkout({
        "idempotency_key": str(uuid.uuid4()),
        "checkout": {
            "amount_money": {"amount": to_cents(amount), "currency": "CAD"},
            "reference_id": reference[:40],
            "note": "Transaction through Pavilion",
            "device_options": {"device_id": device_id, "tip_settings": {"allow_tipping": True}},
        },
    })


def start_checkout(register, amount: Decimal, user=None) -> TerminalCheckout:
    """Start a card payment on this register's Terminal. Raises SquareError (its message can be shown)."""
    terminal = SquareTerminal.for_register(register)
    device_id = terminal.device_id if terminal else settings.SQUARE_DEVICE_ID
    if not device_id:
        raise SquareError(_("No card terminal is paired with this register. Pair one from the Screen button."))
    response = gather_terminal_checkout(amount, device_id, reference=f"register-{register.id}" if register else "")
    if not response.is_success():
        raise SquareError("; ".join(str(err.get("detail", err)) for err in response.errors or []))
    checkout = response.body["checkout"]
    return TerminalCheckout.objects.create(checkout_id=checkout["id"], register=register, terminal=terminal, device_id=device_id,
                                           amount=amount, status=checkout.get("status", TerminalCheckout.PENDING), started_by=user)


def _checkout_changed(row, checkout: dict):
    row.status = checkout.get("status", row.status)
    row.payment_ids = checkout.get("payment_ids") or row.payment_ids
    row.cancel_reason = checkout.get("cancel_reason", row.cancel_reason) or ""
    row.save(update_fields=["status", "payment_ids", "cancel_reason", "updated_at"])
    publish("checkout")
    return row


def checkout_event(checkout: dict):
    """Square's terminal.checkout.updated webhook. Checkouts we didn't start are ignored."""
    row = TerminalCheckout.objects.filter(checkout_id=checkout.get("id", "")).first()
    if row is None:
        logger.info("Ignoring a webhook for checkout %s, which isn't ours", checkout.get("id"))
        return None
    return _checkout_changed(row, checkout)


def refresh_checkout(row) -> TerminalCheckout:
    """Ask Square for a checkout's status (when the register checks and no webhook reached us)."""
    result = square_client.terminal.get_terminal_checkout(row.checkout_id)
    if not result.is_success():
        raise SquareError(result.errors)
    return _checkout_changed(row, result.body["checkout"])


def cancel_checkout(row) -> TerminalCheckout:
    """Stop a checkout on the Terminal (the cashier pressed Cancel)."""
    if row.finished:
        return row
    result = square_client.terminal.cancel_terminal_checkout(row.checkout_id)
    if not result.is_success():
        raise SquareError(result.errors)
    return _checkout_changed(row, result.body["checkout"])


def claim_checkout(checkout_id, register, amount, order):
    """Placing an order uses a card payment once: it must be this register's checkout, completed, for that amount.
    Raises ValueError with a message for the cashier. Call inside the order's transaction."""
    with transaction.atomic():
        row = TerminalCheckout.objects.select_for_update().filter(checkout_id=checkout_id).first()
        if row is None or row.register_id != (register.id if register else None):
            raise ValueError(_("That card payment wasn't taken on this register."))
        if row.status != TerminalCheckout.COMPLETED:
            raise ValueError(_("That card payment hasn't gone through."))
        if row.amount != Decimal(amount):
            raise ValueError(_("That card payment was for $%(amount)s.") % {"amount": row.amount})
        if row.order_id:
            raise ValueError(_("That card payment was already used for order #%(n)s.") % {"n": row.order_id})
        row.order = order
        row.save(update_fields=["order", "updated_at"])
        return row


def to_cents(amount) -> int:
    """Dollars (Decimal/str/float) to integer cents, which is what Square expects."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))

def create_web_payment(token, amount:Decimal, transaction, autocomplete = True):
    payments_api = square_client.payments
    response = payments_api.create_payment({
        "source_id": token,
        "amount_money": {
            "amount": to_cents(amount),
            "currency": "CAD"
        },
        "location_id": location_id(),
        "idempotency_key": str(uuid.uuid4()),  # Important to avoid duplicate charges
        'autocomplete': autocomplete
    })
    if response.is_success():
        # Recorded either way so a rejected order can be voided (authorized) or refunded (captured)
        card = response.body["payment"].get("card_details", {}).get("card", {})
        PaymentAuthorization.objects.create(
            card_brand=card.get("card_brand", ""), card_last4=card.get("last_4", ""),
            payment_id=response.body["payment"]["id"],
            amount=to_cents(amount),
            currency=response.body["payment"]["amount_money"]["currency"],
            status=PaymentAuthorization.AUTHORIZED if not autocomplete else PaymentAuthorization.CAPTURED,
            transaction=transaction
        )
    return response

def capture_authorization(authorization):
    """Capture an authorized (delayed-capture) payment. No-op if it isn't in the authorized state."""
    if authorization.status != PaymentAuthorization.AUTHORIZED:
        return
    result = square_client.payments.complete_payment(authorization.payment_id, body={})
    if not result.is_success():
        raise SquareError(result.errors)
    authorization.status = PaymentAuthorization.CAPTURED
    authorization.save(update_fields=["status"])

def void_authorization(authorization):
    """Give the customer their money back: cancel an authorization, or refund a captured payment."""
    if authorization.status == PaymentAuthorization.AUTHORIZED:
        result = square_client.payments.cancel_payment(authorization.payment_id)
    elif authorization.status == PaymentAuthorization.CAPTURED:
        result = square_client.refunds.refund_payment({
            "idempotency_key": f"refund-{authorization.payment_id}",
            "payment_id": authorization.payment_id,
            "amount_money": {"amount": authorization.amount, "currency": authorization.currency},
            "reason": "Order rejected",
        })
    else:
        return
    if not result.is_success():
        raise SquareError(result.errors)
    authorization.status = PaymentAuthorization.VOIDED
    authorization.save(update_fields=["status"])

ENTRY_METHODS = {"CONTACTLESS": "tap", "EMV": "chip", "SWIPED": "swipe", "KEYED": "keyed", "ON_FILE": "online"}


def fill_card_details(payment):
    """
    Look up which Square payment a register card payment was (from its Terminal checkout) and store the card's
    brand, last 4 digits and how it was presented. Raises SquareError if Square can't say.
    """
    if not payment.square_payment_id:
        result = square_client.terminal.get_terminal_checkout(payment.square_checkout_id)
        if not result.is_success():
            raise SquareError(result.errors)
        ids = result.body["checkout"].get("payment_ids") or []
        if not ids:
            raise SquareError("The checkout has no payment yet")
        payment.square_payment_id = ids[0]
    result = square_client.payments.get_payment(payment.square_payment_id)
    if not result.is_success():
        raise SquareError(result.errors)
    details = result.body["payment"].get("card_details", {})
    card = details.get("card", {})
    payment.card_brand = card.get("card_brand", "")
    payment.card_last4 = card.get("last_4", "")
    payment.entry_method = ENTRY_METHODS.get(details.get("entry_method", ""), "")
    payment.save(update_fields=["square_payment_id", "card_brand", "card_last4", "entry_method"])


def refund_payment(payment_id, amount, key, reason=""):
    """Refund `amount` (dollars) of a captured Square payment. `key` makes retries safe. Returns the refund id."""
    result = square_client.refunds.refund_payment({
        "idempotency_key": key[:45],
        "payment_id": payment_id,
        "amount_money": {"amount": to_cents(amount), "currency": "CAD"},
        "reason": reason[:192],
    })
    if not result.is_success():
        raise SquareError(result.errors)
    return result.body["refund"]["id"]


# ---------- cards on file (catering: saved when the order comes in, charged when it's accepted) ----------

def create_customer(name, email, phone="", company="", key=None):
    """A Square customer to keep cards on. Returns its id."""
    first, _, last = (name or "").partition(" ")
    body = {"idempotency_key": (key or str(uuid.uuid4()))[:45], "given_name": first or "Customer", "family_name": last}
    if email:
        body["email_address"] = email
    if phone:
        body["phone_number"] = phone
    if company:
        body["company_name"] = company
    result = square_client.customers.create_customer(body)
    if not result.is_success():
        raise SquareError(result.errors)
    return result.body["customer"]["id"]


def save_card(token, customer_id, name="", key=None):
    """Keep the card a Web Payments token stands for on `customer_id`. Returns {id, brand, last4}."""
    result = square_client.cards.create_card({
        "idempotency_key": (key or str(uuid.uuid4()))[:45], "source_id": token,
        "card": {"customer_id": customer_id, "cardholder_name": name[:96]},
    })
    if not result.is_success():
        raise SquareError(result.errors)
    card = result.body["card"]
    return {"id": card["id"], "brand": card.get("card_brand", ""), "last4": card.get("last_4", "")}


def charge_card(customer_id, card_id, amount, key, note="", autocomplete=True):
    """
    Charge `amount` (dollars) to a card on file, or only hold it (autocomplete=False: complete_payment() takes it,
    cancel_payment() lets it go; holds last about 7 days). `key` makes retries safe. Returns the payment id.
    """
    result = square_client.payments.create_payment({
        "source_id": card_id, "customer_id": customer_id,
        "amount_money": {"amount": to_cents(amount), "currency": "CAD"},
        "location_id": location_id(), "idempotency_key": key[:45],
        "autocomplete": autocomplete, "note": note[:500],
    })
    if not result.is_success():
        raise SquareError(result.errors)
    return result.body["payment"]["id"]


def disable_card(card_id):
    """Stop a card on file from being charged (a customer removed it)."""
    result = square_client.cards.disable_card(card_id)
    if not result.is_success():
        raise SquareError(result.errors)


def list_cards(customer_id):
    """A customer's cards on file that can still be charged: [{id, brand, last4, exp_month, exp_year}]."""
    result = square_client.cards.list_cards(customer_id=customer_id)
    if not result.is_success():
        raise SquareError(result.errors)
    return [{"id": c["id"], "brand": c.get("card_brand", ""), "last4": c.get("last_4", ""), "exp_month": c.get("exp_month"),
             "exp_year": c.get("exp_year")} for c in result.body.get("cards", []) if c.get("enabled", True)]


def complete_payment(payment_id):
    result = square_client.payments.complete_payment(payment_id, body={})
    if not result.is_success():
        raise SquareError(result.errors)


def cancel_payment(payment_id):
    result = square_client.payments.cancel_payment(payment_id)
    if not result.is_success():
        raise SquareError(result.errors)


class SquareError(Exception):
    """Square rejected a request; `args[0]` holds Square's error list."""
