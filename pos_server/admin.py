from django.contrib import admin
from .models import *

# Register your models here.

admin.site.register(Ingredient)
admin.site.register(Order)
admin.site.register(Menu)
admin.site.register(EligibleDevice)
# admin.site.register(OrderDish)
# admin.site.register(DishComponent)
# admin.site.register(ComponentIngredient)

class StationAdmin(admin.ModelAdmin):
    list_display = ('friendly_name', 'code', 'icon')
    search_fields = ('friendly_name', 'code')
    ordering = ('friendly_name',)

admin.site.register(Station, StationAdmin)

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
    list_display = ('title', 'price', 'get_station', 'in_stock', 'visible_in_menu')
    list_filter = ('new_station', 'in_stock', 'visible_in_menu', 'menu')
    search_fields = ('title', 'description')
    ordering = ('title',)
    
    def get_station(self, obj):
        """Returns the station name for the dish."""
        if obj.new_station:
            return obj.new_station.friendly_name
        return obj.station
    get_station.short_description = 'Station'
    get_station.admin_order_field = 'new_station__friendly_name'

# Register the Dish model with the DishAdmin
admin.site.register(Dish, DishAdmin)
