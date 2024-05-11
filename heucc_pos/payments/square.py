from django.conf import settings
from square.client import Client
import uuid
from django.http import JsonResponse, HttpResponse
import json
from pos_server import globals

# Initialize the Square client
square_client = Client(
    access_token=settings.SQUARE_ACCESS_TOKEN,
    environment=settings.SQUARE_ENVIRONMENT
)

def gather_terminal_checkout(amount:float):
    # Convert amount to the smallest currency unit, e.g., cents
    amount_money = {"amount": amount * 100, "currency": "CAD"}

    checkout_request = {
        "idempotency_key": str(uuid.uuid4()),  # Unique identifier for the request
        "checkout": {
            "amount_money": amount_money,
            "note": "Transaction through HEUCC POS",
            "device_options": {
                "device_id": settings.SQUARE_DEVICE_ID,
                "tip_settings": {
                    "allow_tipping": True  # Or True, as per your requirement
                }
            }
            # Add other necessary fields according to your requirements
        }
    }

    api_response = square_client.terminal.create_terminal_checkout(checkout_request)
    return api_response

def create_device_code():
    try:
        print(settings.SQUARE_LOCATION_ID)
        api_response = square_client.devices.create_device_code({
            "idempotency_key": str(uuid.uuid4()),
            "device_code": {
                "name": "Square terminal",  # Name your device for identification
                "product_type": "TERMINAL_API",
                "location_id": settings.SQUARE_LOCATION_ID
            }
        })

        if api_response.is_success():
            return api_response.body['device_code']['code']
        elif api_response.is_error():
            print(api_response.errors)
    except Exception as e:
        print(f"Error occurred: {e}")

def terminal_checkout(request):
    body = json.loads(request.body)
    amount = float(body["amount"])
    response = gather_terminal_checkout(amount)
    if response.is_success():
        checkout = response.body
        globals.checkout_card_status = checkout["checkout"]["status"]
        return JsonResponse({"message":checkout}, status=200)
    elif response.is_error():
        errors = response.errors
        return JsonResponse({"message":errors}, status=500)
    
def create_web_payment(token, amount:float):
    payments_api = square_client.payments
    response = payments_api.create_payment({
        "source_id": token,
        "amount_money": {
            "amount": int(amount * 100),
            "currency": "CAD"
        },
        "location_id": settings.SQUARE_LOCATION_ID,
        "idempotency_key": str(uuid.uuid4())  # Important to avoid duplicate charges
    })
    return response