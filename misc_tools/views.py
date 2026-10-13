from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from . import funcs
from gtts import gTTS
import io
import subprocess
import tempfile
import os
import random
from django.conf import settings

# Create your views here.

@staff_member_required
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
        color_dict = funcs.get_image_colors(uploaded_image.read(), num_colors)

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
    text = request.GET.get('text', 'Hello')[:300]  # public (the status board uses it); keep it short

    # Generate TTS audio from text
    tts_buffer = io.BytesIO()
    gTTS(text=text, lang='en').write_to_fp(tts_buffer)

    # Pick a random intro chime
    intro_dir_path = os.path.join(settings.BASE_DIR, 'misc_tools', 'static', 'misc_tools', 'announcement_chimes')
    files = [f for f in os.listdir(intro_dir_path) if os.path.isfile(os.path.join(intro_dir_path, f))]
    intro_path = os.path.join(intro_dir_path, random.choice(files))

    # Chime + speech in one MP3. ffmpeg re-encodes both so differing sample rates join cleanly.
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as speech_file:
        speech_file.write(tts_buffer.getvalue())
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", intro_path, "-i", speech_file.name,
             "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1", "-f", "mp3", "pipe:1"],
            capture_output=True, check=True, timeout=30,
        )
    finally:
        os.remove(speech_file.name)
    combined_buffer = io.BytesIO(result.stdout)

    # Create an HTTP response with the combined audio content
    response = HttpResponse(combined_buffer.getvalue(), content_type="audio/mpeg")
    response['Content-Disposition'] = 'attachment; filename="combined_tts.mp3"'

    return response