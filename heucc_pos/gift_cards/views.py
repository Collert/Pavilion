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
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse, HttpResponseForbidden, HttpResponseNotFound
import json
from decimal import Decimal

# Create your views here.

def card_display(request, card_number):
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
    if request.method == "GET":
        templates = CardPreset.objects.all()
        return render(request, "gift_cards/get-card.html", {
            "route":"get_card",
            "cards": templates
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

@csrf_exempt
def card_api(request, card_number):
    if request.method == "GET":
        try:
            card = GiftCard.objects.get(number=card_number)
            return JsonResponse({
                "email":card.email,
                "image":card.image.url,
                "available_balance":card.available_balance,
                "number":card.number
            }, status=200)
        except GiftCard.DoesNotExist:
            return HttpResponseNotFound()
    elif request.method == "POST":
        try:
            card = GiftCard.objects.get(number=card_number)
            body = json.loads(request.body)
            amount = Decimal(body["amount"])
            response = card.charge_card(amount)
            if response == 200:
                return JsonResponse({
                    "message":f"Card charged ${amount}"
                }, status=200)
            elif response == 402:
                return JsonResponse({
                    "message":"Insufficient card balance."
                }, status=402)
        except GiftCard.DoesNotExist:
            return HttpResponseNotFound()