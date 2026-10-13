from django.conf import settings
from square.client import Client
import uuid
from decimal import Decimal
from django.http import JsonResponse, HttpResponse
import json
from django.core.cache import cache
from pavilion.realtime import publish
from .models import PaymentAuthorization, SquareTerminal
from django.utils.dateparse import parse_datetime

# Initialize the Square client
square_client = Client(
    access_token=settings.SQUARE_ACCESS_TOKEN,
    environment=settings.SQUARE_ENVIRONMENT
)

def gather_terminal_checkout(amount:Decimal):
    """
    Initiates a terminal checkout request with Square API.
    This function converts the given amount to the smallest currency unit (e.g., cents)
    and creates a checkout request with necessary details such as idempotency key,
    amount, note, and device options. It then sends the request to the Square API
    to create a terminal checkout.
    Args:
        amount (float): The amount for the transaction in dollars.
    Returns:
        dict: The response from the Square API after creating the terminal checkout.
    """
    # Convert amount to the smallest currency unit, e.g., cents
    amount_money = {"amount": to_cents(amount), "currency": "CAD"}

    checkout_request = {
        "idempotency_key": str(uuid.uuid4()),  # Unique identifier for the request
        "checkout": {
            "amount_money": amount_money,
            "note": "Transaction through Pavilion",
            "device_options": {
                "device_id": SquareTerminal.current_device_id() or settings.SQUARE_DEVICE_ID,
                "tip_settings": {
                    "allow_tipping": True  # Or True, as per your requirement
                }
            }
            # Add other necessary fields according to your requirements
        }
    }

    api_response = square_client.terminal.create_terminal_checkout(checkout_request)
    return api_response

def create_device_code(name="Square Terminal"):
    """Ask Square for a new pairing code and store it. Raises SquareError on failure."""
    response = square_client.devices.create_device_code({
        "idempotency_key": str(uuid.uuid4()),
        "device_code": {
            "name": name,
            "product_type": "TERMINAL_API",
            "location_id": settings.SQUARE_LOCATION_ID
        }
    })
    if not response.is_success():
        raise SquareError(response.errors)
    device_code = response.body["device_code"]
    return SquareTerminal.objects.create(
        name=name,
        device_code_id=device_code["id"],
        code=device_code["code"],
        status=device_code.get("status", "UNPAIRED"),
        pair_by=parse_datetime(device_code["pair_by"]) if device_code.get("pair_by") else None,
    )

def refresh_device_code(terminal):
    """Update a pairing code's status from Square. A newly paired terminal becomes the active one."""
    response = square_client.devices.get_device_code(terminal.device_code_id)
    if not response.is_success():
        raise SquareError(response.errors)
    device_code = response.body["device_code"]
    terminal.status = device_code.get("status", terminal.status)
    if terminal.status == "PAIRED" and device_code.get("device_id"):
        terminal.device_id = device_code["device_id"]
        if not terminal.is_active:
            SquareTerminal.objects.exclude(pk=terminal.pk).update(is_active=False)
            terminal.is_active = True
    terminal.save()
    return terminal

def terminal_checkout(request):
    body = json.loads(request.body)
    amount = Decimal(str(body["amount"]))
    response = gather_terminal_checkout(amount)
    if response.is_success():
        checkout = response.body
        checkout_id = checkout["checkout"]["id"]
        # Store both the status and checkout ID for better tracking
        # Using 60 second timeout to match active orders caching pattern
        cache.set('checkout_card_status', checkout["checkout"]["status"], timeout=180)
        cache.set('current_checkout_id', checkout_id, timeout=180)
        publish("checkout")
        return JsonResponse({"message":checkout}, status=200)
    elif response.is_error():
        errors = response.errors
        return JsonResponse({"message":errors}, status=500)
    
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
        "location_id": settings.SQUARE_LOCATION_ID,
        "idempotency_key": str(uuid.uuid4()),  # Important to avoid duplicate charges
        'autocomplete': autocomplete
    })
    if response.is_success():
        # Recorded either way so a rejected order can be voided (authorized) or refunded (captured)
        PaymentAuthorization.objects.create(
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

class SquareError(Exception):
    """Square rejected a request; `args[0]` holds Square's error list."""
