from django.db import models

# Create your models here.

class Event(models.Model):
    title = models.CharField(max_length=120)
    start = models.DateTimeField()
    end = models.DateTimeField()
    location_title = models.CharField(max_length=120)
    location_address = models.TextField()
    description = models.TextField()
    restaurant_open = models.BooleanField()

class EventTemplate(Event):
    pass