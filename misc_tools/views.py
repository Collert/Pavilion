from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from . import funcs
from gtts import gTTS
import io
from pydub import AudioSegment
import os
import random
from django.conf import settings

# Create your views here.

def dom_image_colors(request):
    """
    Handle GET and POST requests to process an image and extract dominant colors.
    GET:
        Renders the HTML template for uploading an image.
    POST:
        Processes the uploaded image to extract dominant colors.
        - Reads the uploaded image from the request.
        - Saves the image temporarily.
        - Extracts a specified number of dominant colors from the image.
        - Deletes the temporary image file.
        - Returns the dominant colors as a JSON response.
    Args:
        request (HttpRequest): The HTTP request object.
    Returns:
        HttpResponse: Renders the HTML template for GET requests.
        JsonResponse: Returns a JSON response with the dominant colors for POST requests.
    """
    if request.method == "GET":
        return render(request, "misc_tools/get-image-colors.html")
    elif request.method == "POST":
        num_colors = 4
        uploaded_image = request.FILES['image']
        image_path = default_storage.save('temp_image.jpg', ContentFile(uploaded_image.read()))
        
        color_dict = funcs.get_image_colors(image_path, num_colors)

        default_storage.delete(image_path)

        return JsonResponse(color_dict)
    
def tts(request):
    """
    Generate a Text-to-Speech (TTS) audio file combined with an intro audio and return it as an HTTP response.
    This function requires FFMPEG to be installed. Download it here: https://ffmpeg.org/download.html
    Args:
        request (HttpRequest): The HTTP request object containing query parameters.
    Returns:
        HttpResponse: An HTTP response with the combined audio content as an MP3 file.
    Query Parameters:
        text (str): The text to be converted to speech. Defaults to 'Hello'.
    Raises:
        FileNotFoundError: If the intro audio directory or files are not found.
        Exception: For any other exceptions that may occur during audio processing.
    """
    
    # Get the text from query params
    text = request.GET.get('text', 'Hello')

    # Generate TTS audio from text
    tts = gTTS(text=text, lang='en')
    tts_buffer = io.BytesIO()
    tts.write_to_fp(tts_buffer)
    tts_buffer.seek(0)
    tts_audio = AudioSegment.from_file(tts_buffer, format="mp3")

    # Load intro audio
    intro_dir_path  = os.path.join(settings.BASE_DIR, 'misc_tools', 'static', 'misc_tools', 'announcement_chimes')
    files = [f for f in os.listdir(intro_dir_path) if os.path.isfile(os.path.join(intro_dir_path, f))]
    intro_file = random.choice(files)
    intro_path = os.path.join(intro_dir_path, intro_file)
    intro_audio = AudioSegment.from_mp3(intro_path)

    # Combine the intro audio with the TTS audio
    combined_audio = intro_audio + tts_audio

    # Create an in-memory bytes buffer for the combined audio
    combined_buffer = io.BytesIO()
    combined_audio.export(combined_buffer, format="mp3")
    combined_buffer.seek(0)

    # Create an HTTP response with the combined audio content
    response = HttpResponse(combined_buffer.getvalue(), content_type="audio/mpeg")
    response['Content-Disposition'] = 'attachment; filename="combined_tts.mp3"'

    return response