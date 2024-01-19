from django.http import JsonResponse
from django.shortcuts import render
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from . import funcs

# Create your views here.

def index(request):
    if request.method == "GET":
        return render(request, "misc_tools/get-image-colors.html")
    elif request.method == "POST":
        num_colors = 4
        uploaded_image = request.FILES['image']
        image_path = default_storage.save('temp_image.jpg', ContentFile(uploaded_image.read()))
        
        color_dict = funcs.get_image_colors(image_path, num_colors)

        default_storage.delete(image_path)

        return JsonResponse(color_dict)