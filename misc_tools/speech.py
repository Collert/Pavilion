"""Spoken announcements: gTTS speech after a random chime, joined into one MP3 by ffmpeg (which must be installed:
https://ffmpeg.org/download.html). gTTS needs the internet."""
import io
import os
import random
import subprocess
import tempfile

from django.conf import settings

CHIMES = os.path.join(settings.BASE_DIR, "misc_tools", "static", "misc_tools", "announcement_chimes")


def speech_mp3(text, lang="en") -> bytes:
    from gtts import gTTS
    speech = io.BytesIO()
    gTTS(text=text, lang=lang).write_to_fp(speech)
    chimes = [f for f in os.listdir(CHIMES) if os.path.isfile(os.path.join(CHIMES, f))]
    intro = os.path.join(CHIMES, random.choice(chimes))
    # Chime + speech in one MP3. ffmpeg re-encodes both so differing sample rates join cleanly.
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as speech_file:
        speech_file.write(speech.getvalue())
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", intro, "-i", speech_file.name,
             "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1", "-f", "mp3", "pipe:1"],
            capture_output=True, check=True, timeout=30,
        )
    finally:
        os.remove(speech_file.name)
    return result.stdout
