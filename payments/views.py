from django.shortcuts import render
from django.http import JsonResponse
from .square import create_web_payment, capture_payment
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
        amount = request.GET.get('amount', None)
        if amount:
            amount = float("{:.2f}".format(float(amount)))
            transaction = Transaction.objects.create(amount=amount)
        else:
            transaction = None
        confirmation_needed = bool(request.GET.get('confirmation_needed', False))
        return render(request, "payments/square-checkout.html", {
            "location_id":settings.SQUARE_LOCATION_ID,
            "app_id":settings.SQUARE_APPLICATION_ID,
            "amount":int(amount * 100) if amount else None,
            "transaction":transaction,
            "dev_env": settings.DEBUG,
            "confirmation_needed":confirmation_needed
        })
    elif request.method == "POST":
        data = json.loads(request.body)
        transaction = Transaction.objects.get(uuid=data.get('transaction_uuid'))
        transaction.successful = True
        transaction.save()
        return JsonResponse({"status":"marked_paid", "uuid":transaction.uuid})
    elif request.method == "PUT":
        data = json.loads(request.body)
        transaction = Transaction.objects.get(uuid=data.get('transaction_uuid'))
        if not transaction:
            return JsonResponse({"error":"transaction not found"})
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
        amount = int(data.get('amount'))
        confirmation_needed = bool(data.get('confirmation_needed'))
        transaction_uuid = data.get('transaction_uuid')
        transaction = Transaction.objects.get(uuid=transaction_uuid)

        response = create_web_payment(token, float("{:.2f}".format(amount/100)), autocomplete = not confirmation_needed, transaction=transaction)

        if response.is_success():
            return JsonResponse({'status': 'success', 'payment': response.body})
        elif response.is_error():
            return JsonResponse({'status': 'error', 'errors': response.errors})

    return JsonResponse({'status': 'method not allowed'}, status=405)

def capture_web_payment(request):
    """
    Handle web payment capture and deletion requests.

    Args:
        request (HttpRequest): The HTTP request object containing the payment data.

    Returns:
        JsonResponse: A JSON response indicating the status of the operation.

    The function supports two HTTP methods:
    - POST: Captures a payment based on the provided payment_id.
    - DELETE: Deletes a payment authorization based on the provided payment_id.

    POST:
        - Expects a JSON body with a 'payment_id' field.
        - Attempts to capture the payment and update the authorization status to "del".
        - Returns a JSON response with the status 'captured' and the captured payment ID.
        - In case of an error, returns a JSON response with the status 'error' and the error message.

    DELETE:
        - Expects a JSON body with a 'payment_id' field.
        - Updates the authorization status to "del".
        - Returns a JSON response with the status 'deleted' and the authorization ID.
    """
    data = json.loads(request.body)
    if request.method == "POST":
        payment_id = data.get('payment_id')
        try:
            authorization = PaymentAuthorization.objects.get(payment_id=payment_id)
            if authorization.status == "":
                captured_payment = capture_payment(payment_id)
            authorization.status = "del"
            authorization.save()
            return JsonResponse({'status': 'captured', 'payment_id': captured_payment['id']})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    elif request.method == "DELETE":
        payment_id = data.get('payment_id')
        authorization = PaymentAuthorization.objects.get(payment_id=payment_id)
        authorization.status = "del"
        authorization.save()
        return JsonResponse({'status': 'deleted', 'payment_id': authorization.id})