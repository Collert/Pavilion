from django.shortcuts import render
from pos_server.models import *
from .models import *
from django.core import serializers
from django.http import HttpResponseRedirect, StreamingHttpResponse, JsonResponse
from django.urls import reverse
import json
from . import globals
import time
from django.contrib.auth.decorators import login_required

# Create your views here.

@login_required
def log_shopping(request):
    if request.method == "GET":
        ingredients = Ingredient.objects.all()
        return render(request, "inventory/log-shopping.html", {
            "ingredients":ingredients,
            "route":"log_shopping",
            "json": serializers.serialize('json', ingredients),
        })
    elif request.method == "POST":
        total_quantity = int(request.POST["items-total"])
        try:
            receipt = request.FILES['receipt']
        except:
            receipt = None
        new_update = StockUpdate(receipt=receipt)
        new_update.save()
        for item in range(total_quantity):
            title = request.POST[f"item-{item}-title"]
            quantity = int(request.POST[f"item-{item}-quantity"])
            ingredient = Ingredient.objects.get(title=title)
            ingredient.inventory += quantity
            ingredient.save()
            ui = UpdateIngredient(update_obj=new_update, ingredient=ingredient, quantity=quantity)
            ui.save()
        return HttpResponseRedirect(reverse("log_shopping"))
        
@login_required
def shopping_history(request):
    shopping_occasions = StockUpdate.objects.all()
    return render(request, "inventory/shopping-history.html", {
        "history":shopping_occasions,
        "route":"shopping_history"
    })

@login_required
def day_display(request, day_id):
    day = StockUpdate.objects.get(id=day_id)
    return render(request, "inventory/day-display.html", {
        "day":day
    })

@login_required
def crafting(request):
    if request.method == "GET":
        components = Component.objects.filter(self_crafting=False)
        ingredients = Ingredient.objects.all()
        return render(request, "inventory/crafting.html", {
            "route":"crafting",
            "components":components,
            "ingredients":ingredients,
            "ingredient_inventory": collect_ing_stock(ingredients),
        })
    elif request.method == "POST":
        body = json.loads(request.body)
        for component_id in body:
            if body[component_id]:
                craft_component(component_id, body[component_id])
        return JsonResponse({"message":"crafted"})
    elif request.method == "PUT":
        component_id = int(request.body)
        craft_component(component_id, 1)
        return JsonResponse({"message":"crafted"})

@login_required
def component_availability(request):
    menu = Menu.objects.get(is_active=True)
    dishes = Dish.objects.filter(menu=menu)
    if request.method == "GET":
        components = Component.objects.all()
        return render(request, "inventory/component-availability.html", {
            "components":components,
            "dishes":dishes,
            "route":"component_availability"
        })
    elif request.method == "PUT":
        body = json.loads(request.body)
        if body["type"] == "component":
            component = Component.objects.get(pk=body["id"])
            component.in_stock = not component.in_stock
            component.save(force_update_stock = True)
        else:
            dish = Dish.objects.get(pk=body["id"])
            was_forced = dish.force_in_stock
            for dc in dish.dishcomponent_set.all():
                if dc.component.inventory < dc.quantity:
                    dish.force_in_stock = not dish.force_in_stock
                    break
            if not dish.force_in_stock:
                if not was_forced:
                    dish.in_stock = not dish.in_stock
            dish.save()
        return JsonResponse({"dishes":serializers.serialize('json', dishes)})
    

def event_stream():
    while True:
        # print(globals.new_inventory_updates)
        while globals.new_inventory_updates:
            ingredients = Ingredient.objects.all()
            yield f"data: {collect_ing_stock(ingredients)}\n\n"
            time.sleep(2)
            try:
                globals.new_inventory_updates = False
            except:
                pass
        # Send a heartbeat every X seconds
        yield "\n"
        time.sleep(1)

def collect_ing_stock(ingredients):
    inventory = {}
    for ingredient in ingredients:
        inventory[f"{ingredient.id}"] = ingredient.inventory
    return json.dumps(inventory)

def inventory_updates(request):
    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    # response['Cache-Control'] = 'no-cache'
    return response

def craft_component(component_id:int, qty:int):
    component = Component.objects.get(pk=component_id)
    component.inventory += qty
    component.save()
    for ci in component.componentingredient_set.all():
        ci.ingredient.inventory -= ci.quantity * qty
        ci.ingredient.save()