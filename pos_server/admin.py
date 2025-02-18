from django.contrib import admin
from .models import *

# Register your models here.

admin.site.register(Ingredient)
admin.site.register(Order)
admin.site.register(Menu)
admin.site.register(Station)
admin.site.register(EligibleDevice)
# admin.site.register(OrderDish)
# admin.site.register(DishComponent)
# admin.site.register(ComponentIngredient)

class ComponentIngredientInline(admin.TabularInline):
    model = ComponentIngredient
    extra = 1  # Number of empty forms to display

class ComponentAdmin(admin.ModelAdmin):
    inlines = [ComponentIngredientInline]

admin.site.register(Component, ComponentAdmin)

class DishComponentInline(admin.TabularInline):
    model = DishComponent
    extra = 1  # Number of empty forms to display

class DishAdmin(admin.ModelAdmin):
    inlines = [DishComponentInline]
    # You can add more customizations here if needed

# Register the Dish model with the DishAdmin
admin.site.register(Dish, DishAdmin)
