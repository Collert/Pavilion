from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from .models import *
from django.template.loader import render_to_string
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
import base64
from payments.models import Transaction
from misc_tools.funcs import send_template_email
from misc_tools.classes import SendGridEmailData
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

def card_display(request, card_number):
    """
    Display the gift card information along with its barcode.
    Args:
        request (HttpRequest): The HTTP request object.
        card_number (str): The number of the gift card to be displayed.
    Returns:
        HttpResponse: The rendered HTML page with the gift card information and barcode.
    Raises:
        GiftCard.DoesNotExist: If the gift card with the given number does not exist.
    The function performs the following steps:
    1. Retrieves the gift card object from the database using the provided card number.
    2. Creates a barcode for the gift card number using the 'code128' barcode class.
    3. Configures the barcode writer with specific options such as module width, height, quiet zone, text distance, font size, background, and foreground colors.
    4. Saves the generated barcode to a BytesIO stream.
    5. Encodes the barcode image in base64 format to embed it in the HTML template.
    6. Passes the base64-encoded image data and the gift card object to the template context.
    7. Renders the 'gift_cards/card.html' template with the provided context.
    """
    try:
        card = GiftCard.objects.get(number=card_number)
    except:
        return render(request, 'gift_cards/card.html',)
    # Create barcode
    CODE = barcode.get_barcode_class('code128')
    barcode_instance = CODE(card.number, writer=ImageWriter())

    # Configure the writer with desired options
    options = {
        'module_width': 0.2,  # Controls the width of the bars
        'module_height': 15.0,  # Height of the bars
        'quiet_zone': 6.5,  # Margins on the sides
        'text_distance': 5.0,  # Distance between bars and text
        'font_size': 10,  # Font size of text
        'write_text': True,  # Whether to write text
        'background': '#0662A6',
        'foreground': '#E4B423'
    }
    
    # Save barcode to a BytesIO stream with specific options
    buffer = BytesIO()
    barcode_instance.write(buffer, options=options)
    buffer.seek(0)

    # Encode the image in base64 to embed in HTML
    image_data = base64.b64encode(buffer.read()).decode('utf-8')
    buffer.close()

    # Pass the base64-encoded image data to the template
    context = {
        'image_data': image_data,
        "card":card
    }
    return render(request, 'gift_cards/card.html', context)

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
    if request.method == "GET":
        templates = CardPreset.objects.all()
        subfolders = ['presets', 'personal']
        image_urls = []
        for subfolder in subfolders:
            folder = f"files/gift-cards/{subfolder}"
            try:
                _dirs, file_names = default_storage.listdir(folder)
            except FileNotFoundError:
                continue
            for file_name in file_names:
                if file_name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg')):
                    name = f"{folder}/{file_name}"
                    # "name" is what gets stored on the GiftCard, "url" is what the browser loads
                    image_urls.append({"name": name, "url": default_storage.url(name)})
        return render(request, "gift_cards/get-card.html", {
            "route":"get_card",
            "cards": templates,
            "card_images":image_urls
        })
    elif request.method == "POST":
        uuid = request.POST["transaction_uuid"]
        image_path = request.POST["image"]
        email = request.POST["email"]
        amount = float(request.POST["amount"])
        name = request.POST["name"]
        sender = request.POST["sender"]
        gifted = request.POST["gifted"] == "true"
        try:
            transaction = Transaction.objects.get(uuid=uuid)
            transaction.delete()
            card = create_card(request, image_path, email, amount, name, sender, gifted)
        except:
            card = GiftCard.objects.filter(email=email, image=image_path, available_balance=amount).first()
        return render(request, "gift_cards/card-confirm.html", {
            "card":card
        })
    
def create_card(request, image_path, email, amount, name, sender, gifted):
    """
    Creates a new gift card and sends a confirmation email.
    Args:
        request (HttpRequest): The HTTP request object.
        image_path (str): The path to the image associated with the gift card.
        email (str): The recipient's email address.
        amount (float): The initial balance of the gift card.
        name (str): The recipient's name.
        sender (str): The sender's name (used if the card is gifted).
        gifted (bool): Indicates whether the card is a gift.
    Returns:
        GiftCard: The created GiftCard object.
    """
    card = GiftCard.objects.create(email=email, image=image_path, available_balance=amount)
    # Generate the card link
    card_link = request.build_absolute_uri(reverse('card_display', args=[card.number]))

    email_dynamic_data = {
        "name":name,
        "card_url":card_link
    }

    email_data = SendGridEmailData(
        template_id="d-be1559e433da4d6eba67b8b9c377b3d4" if gifted else "d-8e41ef2b2f3145838d51e9bf0a40b420",
        from_email="restaurant@uahelp.ca"
    )
    if gifted:
        email_dynamic_data["sender"] = sender
    email_data.add_recipient(
        email=email,
        name=name,
        dynamic_data=email_dynamic_data
    )
    # Send confirmation email
    send_template_email(email_data)
    return card

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

def email_card_confirmation(request):
    return render(request, "gift_cards/emails/card-confirm.html", {
        "name":"Jane",
        "gifted":False,
        "sender_name":"Joe",
        "card_link":request.build_absolute_uri(reverse('card_display', "12345"))
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
            card = GiftCard.objects.get(number=card_number)
            data = {
                "image":card.image.url if card.image else None,
                "available_balance":card.available_balance,
                "number":card.number
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
            card = GiftCard.objects.select_for_update().get(number=card_number)
            response = card.charge_card(amount)
        if response == 200:
            return JsonResponse({"message":f"Card charged ${amount}"}, status=200)
        return JsonResponse({"message":"Insufficient card balance."}, status=402)
    except GiftCard.DoesNotExist:
        return HttpResponseNotFound()
