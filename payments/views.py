from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.shortcuts import render
from django.http import JsonResponse
from . import square
from .square import create_web_payment
from django.conf import settings
import json
from .models import Transaction, PaymentAuthorization

# Create your views here.

def web_payment(request):
    """
    Handles web payment requests for GET, POST, and PUT methods.

    GET:
        Retrieves the amount from the request, formats it, and creates a transaction.
        Renders the payment page with necessary details.
        Query Parameters:
            - amount (str): The amount for the transaction.
            - confirmation_needed (bool): Flag indicating if confirmation is needed.
        Returns:
            HttpResponse: Rendered payment page with transaction details.

    POST:
        Marks a transaction as successful based on the provided transaction UUID.
        Request Body:
            - transaction_uuid (str): The UUID of the transaction to mark as paid.
        Returns:
            JsonResponse: Status of the transaction update.

    PUT:
        Checks the payment status of a transaction based on the provided transaction UUID.
        Request Body:
            - transaction_uuid (str): The UUID of the transaction to check.
        Returns:
            JsonResponse: Payment status of the transaction or error if not found.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse or JsonResponse: Depending on the request method.
    """
    if request.method == "GET":
        try:
            amount = Decimal(request.GET.get('amount', '')).quantize(Decimal("0.01"))
            if amount <= 0:
                raise InvalidOperation
        except InvalidOperation:
            amount = None
        transaction = Transaction.objects.create(amount=amount) if amount else None
        confirmation_needed = bool(request.GET.get('confirmation_needed', False))
        return render(request, "payments/square-checkout.html", {
            "location_id": square.location_id(),
            "app_id":settings.SQUARE_APPLICATION_ID,
            "amount":int(amount * 100) if amount else None,  # cents
            "transaction":transaction,
            "dev_env": settings.DEBUG,
            "confirmation_needed":confirmation_needed
        })
    elif request.method in ("POST", "PUT"):
        # Read-only status check. Only process_web_payment can mark a transaction paid.
        data = json.loads(request.body)
        transaction = Transaction.objects.filter(uuid=data.get('transaction_uuid')).first()
        if not transaction:
            return JsonResponse({"error":"transaction not found"}, status=404)
        return JsonResponse({"paid":transaction.successful, "uuid":transaction.uuid})

def process_web_payment(request):
    """
    Process a web payment request.
    This view handles POST requests to process a web payment. It expects the request body to contain
    JSON data with the following fields:
    - token: The payment token.
    - amount: The payment amount in cents.
    - confirmation_needed: A boolean indicating if confirmation is needed.
    - transaction_uuid: The UUID of the transaction.
    The function retrieves the transaction using the provided UUID and creates a web payment using
    the provided token and amount. If the payment is successful, it returns a JSON response with
    the status 'success' and the payment details. If there is an error, it returns a JSON response
    with the status 'error' and the error details. If the request method is not POST, it returns a
    JSON response with the status 'method not allowed' and a 405 status code.
    Args:
        request (HttpRequest): The HTTP request object.
    Returns:
        JsonResponse: A JSON response indicating the result of the payment processing.
    """
    if request.method == 'POST':
        data = json.loads(request.body)
        token = data.get('token')
        confirmation_needed = data.get('confirmation_needed') in (True, "True", "true")
        with db_transaction.atomic():
            transaction = Transaction.objects.select_for_update().filter(uuid=data.get('transaction_uuid')).first()
            if transaction is None:
                return JsonResponse({'status': 'error', 'errors': ['Unknown transaction']}, status=404)
            if transaction.successful:
                return JsonResponse({'status': 'error', 'errors': ['This transaction is already paid']}, status=409)
            # Charge the amount fixed when the popup opened, never an amount sent by the browser
            response = create_web_payment(token, transaction.amount, autocomplete = not confirmation_needed, transaction=transaction)
            if response.is_success():
                transaction.successful = True
                transaction.save(update_fields=["successful"])
                return JsonResponse({'status': 'success', 'payment': response.body})
            return JsonResponse({'status': 'error', 'errors': response.errors})

    return JsonResponse({'status': 'method not allowed'}, status=405)



def save_card(request):
    """
    Keep a card on file for later (a catering request is charged when it's agreed, weeks after it's sent).
    GET: Square's card form (payments/save-card.html, in an iframe). POST JSON {"token"} from it: creates the Square
    customer and card and keeps {"customer", "id", "brand", "last4"} in the session as "saved_card" for the page that
    asked. In DEBUG, {"demo": true} saves a made-up card ("demo:" ids, never sent to Square).
    """
    from . import square
    if request.method != "POST":
        return render(request, "payments/save-card.html", {"location_id": square.location_id(),
                                                           "app_id": settings.SQUARE_APPLICATION_ID, "dev_env": settings.DEBUG})
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"message": "Bad request"}, status=400)
    if data.get("demo") and settings.DEBUG:
        import uuid as uuid_
        card = {"customer": "demo:cust-test", "id": f"demo:card-{uuid_.uuid4().hex[:8]}", "brand": "VISA", "last4": "1111"}
        _keep(request, card)
        return JsonResponse({"ok": True})
    token = str(data.get("token") or "")
    if not token:
        return JsonResponse({"message": "No card"}, status=400)
    user = request.user if request.user.is_authenticated else None
    known = getattr(getattr(user, "customer_profile", None), "square_customer_id", "") if user else ""
    try:
        customer = known or square.create_customer(user.get_full_name() if user else "", user.email if user else "")
        card = square.save_card(token, customer, user.get_full_name() if user else "")
    except square.SquareError:
        return JsonResponse({"message": "The card was declined. Try another one."}, status=400)
    _keep(request, {"customer": customer, **card})
    return JsonResponse({"ok": True})


def _keep(request, card):
    """The page that asked uses the session's card; a signed-in customer's account keeps it too."""
    if request.user.is_authenticated:
        from online_store.account import save_customer_card
        saved = save_customer_card(request.user, card)
        card = {**card, "saved": saved.id}
    request.session["saved_card"] = card
