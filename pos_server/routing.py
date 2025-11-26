"""
WebSocket URL routing for pos_server app.
"""

from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/orders/$', consumers.OrderConsumer.as_asgi()),
    re_path(r'ws/order-progress/$', consumers.OrderProgressConsumer.as_asgi()),
]
