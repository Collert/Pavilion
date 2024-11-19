from django.shortcuts import render
from django.http import JsonResponse
from .square import create_web_payment, capture_payment
from django.conf import settings
import json
from .models import Transaction, PaymentAuthorization

# Create your views here.

def web_payment(request):
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