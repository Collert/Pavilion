from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
import logging

from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.http import JsonResponse
from . import square
from .square import create_web_payment
from django.conf import settings
import json
from .models import Transaction, PaymentAuthorization

logger = logging.getLogger(__name__)

# Create your views here.

def card_page_context(request, amount, pay_url, extra=None, mode="sheet") -> dict:
    """What the card form pages need: the store theme's look, and the card form's config (payments/_card_form.html)."""
    from online_store import themes
    from .square_oauth import environment
    cur = themes.current(request)
    return {
        # css_vars checks every value against its param (online_store/themes.py), so it's safe in a style tag
        "fonts": cur.theme.fonts, "css_vars": mark_safe(themes.css_vars(cur.theme, cur.values)), "store_name": cur.store["name"],
        "config": {"appId": settings.SQUARE_APPLICATION_ID, "locationId": square.location_id(), "sandbox": environment() == "sandbox",
                   "amountText": f"${amount:,.2f}", "payUrl": pay_url, "extra": extra or {}, "mode": mode, "store": cur.store["name"]},
    }


def web_payment(request):
    """
    The store's "Pay by card" (payments/templates/payments/square-checkout.html, in a sheet's iframe). GET
    ?amount=12.50[&confirmation_needed=1][&save_card=1]: a Transaction for that amount and the card form, which pays
    through process_web_payment and then tells the page (save_card: a signed-in customer keeps the card). POST/PUT {transaction_uuid}: whether it's paid (read-only; only
    process_web_payment marks a transaction paid).
    """
    if request.method == "GET":
        try:
            amount = Decimal(request.GET.get('amount', '')).quantize(Decimal("0.01"))
            if amount <= 0:
                raise InvalidOperation
        except InvalidOperation:
            amount = None
        if amount is None:
            return render(request, "payments/square-checkout.html", {"config": None})
        transaction = Transaction.objects.create(amount=amount)
        confirmation_needed = bool(request.GET.get('confirmation_needed', False))
        save_card = request.user.is_authenticated and request.GET.get("save_card") == "1"
        return render(request, "payments/square-checkout.html", card_page_context(
            request, amount, reverse("process-web-payment"),
            extra={"transaction_uuid": str(transaction.uuid), "confirmation_needed": confirmation_needed, "save_card": save_card}))
    elif request.method in ("POST", "PUT"):
        # Read-only status check. Only process_web_payment can mark a transaction paid.
        data = json.loads(request.body)
        transaction = Transaction.objects.filter(uuid=data.get('transaction_uuid')).first()
        if not transaction:
            return JsonResponse({"error":"transaction not found"}, status=404)
        return JsonResponse({"paid":transaction.successful, "uuid":transaction.uuid})


def phone_pay(request, link):
    """
    Paying at the counter on the customer's phone (payments/templates/payments/phone_pay.html): the register shows a
    QR code for this page when it has no Square Terminal (payments.TerminalCheckout, kind phone). GET: what's being
    paid for and the card form, or that it's paid or closed. POST {token}: charge exactly the amount the register
    asked for, once (square.charge_phone); the register hears about it on "checkout".
    """
    from admin_portal.models import StoreSettings
    from .models import TerminalCheckout
    row = get_object_or_404(TerminalCheckout, kind=TerminalCheckout.PHONE, link=link)
    if request.method == "POST":
        try:
            token = json.loads(request.body or "{}").get("token", "")
            if not token:
                raise square.SquareError(_("Enter the card first."))
            square.charge_phone(row, token)
        except square.SquareError as e:
            return JsonResponse({"status": "error", "message": str(e.args[0]) if e.args else _("The payment didn’t go through.")}, status=402)
        except Exception:
            logger.exception("A phone payment failed")
            return JsonResponse({"status": "error", "message": _("The payment didn’t go through. Try again.")}, status=502)
        return JsonResponse({"status": "success"})
    closed = row.status == TerminalCheckout.CANCELED or square.phone_expired(row)
    context = card_page_context(request, row.amount, request.path, mode="page")
    return render(request, "payments/phone_pay.html", {**context, "row": row, "closed": closed, "store": StoreSettings.load(),
                                                       "amount_text": f"${row.amount:,.2f}"})


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
            if data.get("save_card") in (True, "true") and request.user.is_authenticated:
                try:
                    _save_and_charge(request.user, token, transaction, autocomplete=not confirmation_needed)
                except square.SquareError as e:
                    return JsonResponse({'status': 'error', 'errors': e.args[0] if e.args and isinstance(e.args[0], list) else []})
                transaction.successful = True
                transaction.save(update_fields=["successful"])
                return JsonResponse({'status': 'success'})
            # Charge the amount fixed when the popup opened, never an amount sent by the browser
            response = create_web_payment(token, transaction.amount, autocomplete = not confirmation_needed, transaction=transaction)
            if response.is_success():
                transaction.successful = True
                transaction.save(update_fields=["successful"])
                return JsonResponse({'status': 'success', 'payment': response.body})
            return JsonResponse({'status': 'error', 'errors': response.errors})

    return JsonResponse({'status': 'method not allowed'}, status=405)



def _save_and_charge(user, token, transaction, autocomplete):
    """
    A signed-in customer ticked "Save this card": keep it on their Square customer (a SavedCard here holds its brand
    and last 4), then charge it for the transaction's amount, the same hold or charge as create_web_payment.
    """
    from online_store.account import save_customer_card
    profile = getattr(user, "customer_profile", None)
    name = user.get_full_name()
    customer = (profile.square_customer_id if profile else "") or square.create_customer(name, user.email, key=f"cust-{transaction.uuid}")
    card = square.save_card(token, customer, name, key=f"keep-{transaction.uuid}")
    save_customer_card(user, {"customer": customer, **card})
    payment_id = square.charge_card(customer, card["id"], transaction.amount, key=f"web-{transaction.uuid}", note="Online order",
                                    autocomplete=autocomplete)
    PaymentAuthorization.objects.create(
        payment_id=payment_id, amount=square.to_cents(transaction.amount), currency="CAD", transaction=transaction,
        status=PaymentAuthorization.CAPTURED if autocomplete else PaymentAuthorization.AUTHORIZED,
        card_brand=card.get("brand", ""), card_last4=card.get("last4", ""))


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
