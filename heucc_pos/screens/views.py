from django.shortcuts import render
import random
import os
from django.conf import settings
from pos_server.models import Dish, Menu
from misc_tools.funcs import get_image_colors, interpolate_color, generate_qr_code

# Create your views here.

def pick_random_template(type:str):
    template_folder = os.path.join(settings.BASE_DIR, 'screens', 'templates', 'screens', type)
    template_names = [f for f in os.listdir(template_folder) if f.endswith('.html')]
    return random.choice(template_names)

def product_showcase(request):
    menu = Menu.objects.get(is_active = True)
    dishes = Dish.objects.filter(menu=menu).all()
    while True:
        try:
            dish = random.choice(dishes)
            print(f"trying: {dish.title}")
            primary_color = get_image_colors(dish.image.path, 1)['Color 1']
            bg_color = interpolate_color(primary_color)
            break
        except ValueError:
            pass
    data = "https://example.com"  # Data for the QR code
    qr_code_base64 = generate_qr_code(data, fill_color=bg_color, back_color=primary_color)
    return render(request, f"screens/product_showcase/{pick_random_template("product_showcase")}", {
        "dish":dish,
        "primary_color":primary_color,
        "background_color":bg_color,
        "qr_code": qr_code_base64
    })