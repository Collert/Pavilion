from django.db import models

# Create your models here.

class Event(models.Model):
    title = models.CharField(max_length=120)
    start = models.DateTimeField()
    end = models.DateTimeField()
    location_title = models.CharField(max_length=120, null=True, blank=True)
    location_address = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    restaurant_open = models.BooleanField(default=True)
    in_person_open = models.BooleanField()
    online_open = models.BooleanField()
    delivery_open = models.BooleanField()

class EventTemplate(Event):
    pass