from django.shortcuts import render
from .models import *
from django.shortcuts import render
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
import base64

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