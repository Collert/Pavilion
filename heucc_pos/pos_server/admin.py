from django.contrib import admin
from .models import *

# Register your models here.

admin.site.register(Dish)
admin.site.register(Order)
admin.site.register(Menu)
# admin.site.register(OrderDish)