from django.contrib import admin
from .models import *
from .notifications import PushSubscription

# Register your models here.

admin.site.register(Delivery)
admin.site.register(PushSubscription)