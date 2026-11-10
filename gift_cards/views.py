from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.csrf import csrf_exempt
from .models import *
from django.template.loader import render_to_string
import re
import barcode
from django.views.decorators.cache import never_cache
from payments.models import PaymentError, Transaction
import logging

logger = logging.getLogger(__name__)
from misc_tools.email import send as send_email
from django.urls import reverse
from django.db import transaction as db_transaction
from django.http import HttpResponseNotAllowed, HttpResponseRedirect, JsonResponse, HttpResponse, HttpResponseForbidden, HttpResponseNotFound
import json
from decimal import Decimal
import os
from django.core.files.storage import default_storage
from django.conf import settings
from django.utils.translation import gettext_lazy as _

# Create your views here.

def barcode_bars(digits: str) -> dict:
    """The Code 128 bars of `digits` as runs of dark modules ({"width", "bars": [(x, w)]}), drawn as an SVG in the
    template so the barcode stays sharp at any size and on any screen. `viewbox` leaves the quiet zone scanners
    need: ten blank modules on each side."""
    modules = barcode.get_barcode_class("code128")(digits).build()[0]
    bars = [(m.start(), len(m.group())) for m in re.finditer("1+", modules)]
    return {"width": len(modules), "bars": bars, "viewbox": f"-10 0 {len(modules) + 20} 40"}


@never_cache
def card_display(request, card_number):
    """
    A gift card's own page (the link in its first email and on the store's confirmation): the card's design, its
    barcode and 16-digit number to show at the counter, and what's left on it. Only the number's hash is stored,
    so the number comes from the link itself; a link that matches no card shows "Card not found" (404).
    """
    from admin_portal.models import StoreSettings
    store = StoreSettings.load()
    card = GiftCard.find(card_number)
    context = {"store": store, "card": card}
    status = 404
    if card is not None:
        digits = "".join(c for c in card_number if c.isdigit())
        context.update(barcode=barcode_bars(digits), number=" ".join(digits[i:i + 4] for i in range(0, len(digits), 4)),
                       picture=card.picture_url(), design=card.design)
        status = 200
    response = render(request, "gift_cards/card.html", context, status=status)
    response["Referrer-Policy"] = "no-referrer"  # the card's number is in the address
    response["X-Robots-Tag"] = "noindex"
    return response

def balance(request, token):
    """A gift card's balance from the signed link in a resent email (gift_cards/giftcards.py: balance_link)."""
    from admin_portal.models import StoreSettings
    from .giftcards import from_token
    card = from_token(token)
    return render(request, "gift_cards/balance.html", {"card": card, "store": StoreSettings.load().name}, status=200 if card else 404)


def get_card(request):
    """
    Handle GET and POST requests for gift cards.

    GET:
    - Retrieves all card presets and images from specified subfolders.
    - Renders the 'gift_cards/get-card.html' template with the card presets and image URLs.

    POST:
    - Retrieves transaction details from the request.
    - Deletes the transaction if it exists and creates a new gift card.
    - If the transaction does not exist, retrieves an existing gift card based on the provided details.
    - Renders the 'gift_cards/card-confirm.html' template with the created or retrieved gift card.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered HTML response.
    """
    if request.method == "GET":  # gift cards are bought on the store's gift card page now
        from django.shortcuts import redirect
        return redirect("store_gift_cards")
    elif request.method == "POST":
        uuid = request.POST.get("transaction_uuid")
        image_path = request.POST.get("image", "")
        email = request.POST.get("email", "")
        name = request.POST.get("name", "")
        sender = request.POST.get("sender", "")
        gifted = request.POST.get("gifted") == "true"
        try:
            with db_transaction.atomic():
                # The card is worth what was actually paid, not what the form says
                transaction = Transaction.consume(uuid, Decimal("0.01"))
                card = GiftCard.objects.create(email=email, image=image_path, available_balance=transaction.amount, recipient=name[:60],
                                               buyer=(sender or name)[:80], source=GiftCard.ONLINE)
                GiftCardEvent.objects.create(card=card, kind=GiftCardEvent.ISSUE, amount=card.original, balance=card.original,
                                             note=str(_("Bought online")))
        except PaymentError as e:
            return render(request, "gift_cards/card-confirm.html", {"error": str(e)}, status=402)
        send_card_email(request, card, email, name, sender, gifted)
        return render(request, "gift_cards/card-confirm.html", {
            "card":card
        })
    
def send_card_email(request, card, email, name, sender, gifted):
    """
    Email the new card's link to the recipient. A failed email is logged, not raised: the customer
    has paid and the card exists, and the confirmation page shows the link too.
    """
    # Generate the card link
    card_link = request.build_absolute_uri(reverse('card_display', args=[card.number]))

    data = {"name": name, "card_url": card_link}
    if gifted:
        data["sender"] = sender
    try:
        send_email("gift_card_gifted" if gifted else "gift_card", email, data)
    except Exception:
        logger.exception("Failed to send gift card email for card •• %s", card.last4)

def special(request):
    return render(request, "gift_cards/special.html", {
        "route":"special"
    })

def new_card_confirmation(request):
    """
    Handles the confirmation view for a new gift card.

    This view retrieves the gift card based on the provided card ID from the request's GET parameters.
    If the card ID is present, it fetches the corresponding GiftCard object from the database.
    The view then renders the "gift_cards/card-confirm.html" template with the gift card context.

    Args:
        request (HttpRequest): The HTTP request object containing GET parameters.

    Returns:
        HttpResponse: The rendered HTML response with the gift card context.
    """
    card_id = request.GET.get('cardId')
    card = GiftCard.objects.get(pk=card_id) if card_id else None
    return render(request, "gift_cards/card-confirm.html", {
        "card":card
    })

@login_required
@user_passes_test(lambda u: u.is_superuser)
def email_card_confirmation(request):
    """Preview of the gift card email template."""
    return render(request, "gift_cards/emails/card-confirm.html", {
        "name":"Jane",
        "gifted":False,
        "sender_name":"Joe",
        "card_number":"1234567890123456",
    })

def card_api(request, card_number):
    """
    Handle API requests for gift card operations.

    Args:
        request (HttpRequest): The HTTP request object.
        card_number (str): The number of the gift card.

    Returns:
        JsonResponse: A JSON response containing gift card details or operation results.
        HttpResponseNotFound: If the gift card does not exist.

    Methods:
        GET: Retrieve the card's image URL, available balance, and number. The owner's email is only
            included for logged-in staff. Public because customers look up their cards at checkout.
        POST: Charge the gift card with a specified amount (staff only, CSRF-protected). Orders normally
            charge cards server-side; this is for manual adjustments.
    """
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.method == "POST" and not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponseForbidden()
    try:
        if request.method == "GET":
            card = GiftCard.find(card_number)
            if card is None:
                raise GiftCard.DoesNotExist
            data = {
                "image": card.picture_url() or None,
                "available_balance": card.available_balance,
                "last4": card.last4,
                "frozen": card.frozen,
            }
            if request.user.is_authenticated and request.user.is_staff:
                data["email"] = card.email
            return JsonResponse(data, status=200)
        # POST
        try:
            amount = Decimal(str(json.loads(request.body)["amount"]))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            return JsonResponse({"message":"Invalid amount."}, status=400)
        if amount <= 0:
            return JsonResponse({"message":"Invalid amount."}, status=400)
        with db_transaction.atomic():
            card = GiftCard.find(card_number, lock=True)
            if card is None:
                raise GiftCard.DoesNotExist
            response = card.charge_card(amount, by=request.user)
        if response == 200:
            return JsonResponse({"message":f"Card charged ${amount}"}, status=200)
        return JsonResponse({"message":"Insufficient card balance."}, status=402)
    except GiftCard.DoesNotExist:
        return HttpResponseNotFound()
