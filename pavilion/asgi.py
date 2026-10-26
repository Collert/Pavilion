"""
ASGI config for pavilion project: Django for HTTP (pages, and the SSE streams in pavilion/realtime.py), and the voice
channels' WebSockets at /ws/voice/<channel id> (audio/voice.py). Daphne serves both.
"""

import os
import re

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pavilion.settings')

django_application = get_asgi_application()

VOICE_PATH = re.compile(r"^/ws/voice/(\d+)$")


async def application(scope, receive, send):
    if scope["type"] == "websocket":
        match = VOICE_PATH.match(scope.get("path", ""))
        if match:
            from audio.voice import voice_socket
            return await voice_socket(scope, receive, send, int(match.group(1)))
        await receive()
        await send({"type": "websocket.close", "code": 4404})
        return
    return await django_application(scope, receive, send)
