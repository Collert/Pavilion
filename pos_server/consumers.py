"""
WebSocket consumers for real-time order updates.

This module provides WebSocket consumers that enable real-time communication
between the server and clients for order updates. It replaces the polling-based
approach with a push-based WebSocket approach.
"""

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils.timezone import now, localdate
from django.db.models import Q


class OrderConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time order updates.
    
    Clients connect to this consumer to receive real-time notifications
    when orders are created, updated, or deleted.
    """
    
    async def connect(self):
        """Handle WebSocket connection."""
        self.room_group_name = 'orders'
        
        # Join the orders group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # Send initial active orders on connection
        orders = await self.get_active_orders()
        await self.send(text_data=json.dumps({
            'type': 'initial_orders',
            'orders': orders
        }))
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        # Leave the orders group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        # Currently, clients don't send messages, but this can be extended
        pass
    
    async def order_update(self, event):
        """
        Handle order update events from the channel layer.
        
        This is called when an order is created, updated, or deleted.
        """
        await self.send(text_data=json.dumps({
            'type': event['update_type'],
            'order': event['order']
        }))
    
    async def order_delete(self, event):
        """
        Handle order delete events from the channel layer.
        """
        await self.send(text_data=json.dumps({
            'type': 'order_deleted',
            'order_id': event['order_id']
        }))
    
    @database_sync_to_async
    def get_active_orders(self):
        """
        Get all active orders for today.
        
        Returns a list of serialized order data.
        """
        from .models import Order, OrderDish
        from .views import collect_order
        
        today = localdate()
        active_orders = Order.objects.filter(
            Q(start_time__lte=now()) &
            (Q(kitchen_status=0) |
            Q(kitchen_status=1) |
            Q(bar_status=0) |
            Q(bar_status=1) |
            Q(gng_status=0) |
            Q(gng_status=1) |
            Q(picked_up=False))
        ).filter(timestamp__date=today)
        
        return [collect_order(order) for order in active_orders]


class OrderProgressConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for customer-facing order progress screen.
    
    This consumer provides real-time updates for the order status display
    that customers see, showing which orders are in progress and ready.
    """
    
    async def connect(self):
        """Handle WebSocket connection."""
        self.room_group_name = 'order_progress'
        
        # Join the order progress group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # Send initial orders on connection
        orders = await self.get_progress_orders()
        await self.send(text_data=json.dumps({
            'type': 'initial_orders',
            'orders': orders
        }))
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        pass
    
    async def order_update(self, event):
        """Handle order update events."""
        await self.send(text_data=json.dumps({
            'type': event['update_type'],
            'order': event['order']
        }))
    
    async def order_delete(self, event):
        """Handle order delete events."""
        await self.send(text_data=json.dumps({
            'type': 'order_deleted',
            'order_id': event['order_id']
        }))
    
    @database_sync_to_async
    def get_progress_orders(self):
        """
        Get orders for the progress display (in progress and ready).
        """
        from .models import Order
        from .views import collect_order
        
        today = localdate()
        
        # Get in progress orders (kitchen_status = 1)
        in_progress = Order.objects.filter(
            kitchen_status=1,
            timestamp__date=today
        )
        
        # Get ready orders (kitchen_status = 2 and not picked up)
        ready = Order.objects.filter(
            Q(kitchen_status=2) & Q(picked_up=False),
            timestamp__date=today
        )
        
        in_progress_data = [collect_order(order) for order in in_progress]
        ready_data = [collect_order(order) for order in ready]
        
        return {
            'in_progress': in_progress_data,
            'ready': ready_data
        }
