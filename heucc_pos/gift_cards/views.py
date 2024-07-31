from django.shortcuts import render
from .models import *
from django.shortcuts import render
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
import base64
from payments.models import Transaction
from misc_tools.funcs import send_html_email

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
        try:
            transaction = Transaction.objects.get(uuid=uuid)
            transaction.delete()
            card = GiftCard.objects.create(email=email, image=image_path, available_balance=amount)
            
        except:
            card = GiftCard.objects.filter(email=email, image=image_path, available_balance=amount).first()
        return render(request, "gift_cards/card-confirm.html", {
            "card":card
        })

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
        "gifted":True,
        "sender_name":"Joe",
        "card_number":"12345"
    })