"""
ASGI config for pavilion project: Django for HTTP (pages, and the SSE streams in pavilion/realtime.py), the voice
channels' WebSockets at /ws/voice/<channel id> (audio/voice.py) and the visual dongles' at /ws/dongle
(devices/socket.py). Daphne serves both.
"""

import os
import re

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pavilion.settings')

django_application = get_asgi_application()

VOICE_PATH = re.compile(r"^/ws/voice/(\d+)$")


async def _lifespan(receive, send):
    """Uvicorn announces startup and shutdown; Django's handler only does HTTP, so answer here (nothing to set up)."""
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        return await _lifespan(receive, send)
    if scope["type"] == "websocket":
        match = VOICE_PATH.match(scope.get("path", ""))
        if match:
            from audio.voice import voice_socket
            return await voice_socket(scope, receive, send, int(match.group(1)))
        if scope.get("path") == "/ws/dongle":
            from devices.socket import dongle_socket
            return await dongle_socket(scope, receive, send)
        await receive()
        await send({"type": "websocket.close", "code": 4404})
        return
    return await django_application(scope, receive, send)
