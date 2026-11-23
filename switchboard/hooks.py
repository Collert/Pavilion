"""
Events that come from a row being written (connected in apps.py: ready). These rows are each the record of the thing
itself, wherever in Pavilion it was done: a line of a gift card's history, a staff card, cash moved at a drawer, an
event on the store's calendar, a service booking. Everything else is sent from the function that does it (events.py).

Receivers only hand the row to events.py, whose functions never raise and do nothing until the transaction commits.
"""
from django.db.models.signals import post_save


def connect():
    from events.models import Event
    from gift_cards.models import GiftCardEvent, StaffCard
    from pos_server.models import CashMovement
    from pos_server.signals import order_ready
    from services.models import Booking

    from . import events

    def ready_order(order, **kwargs):
        events.order_ready(order)

    def made(send):
        def receiver(sender, instance, created, raw=False, **kwargs):
            if created and not raw:
                send(instance)
        return receiver

    order_ready.connect(ready_order, dispatch_uid="switchboard-order-ready", weak=False)  # local functions: keep them alive
    for model, send in ((GiftCardEvent, events.gift_card_history), (StaffCard, events.staff_card_issued), (CashMovement, events.cash_moved),
                        (Event, events.store_event_created), (Booking, events.service_booked)):
        post_save.connect(made(send), sender=model, dispatch_uid=f"switchboard-{model.__name__}", weak=False)
