from django.dispatch import Signal

# Sent with `order` when the last station working on an order finishes it.
order_ready = Signal()
