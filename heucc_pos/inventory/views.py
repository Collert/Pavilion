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
        receipt = request.FILES['receipt']
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
        components = Component.objects.all()
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
                component = Component.objects.get(pk=component_id)
                component.inventory += body[component_id]
                component.save()
                for ci in component.componentingredient_set.all():
                    ci.ingredient.inventory -= ci.quantity * body[component_id]
                    ci.ingredient.save()
        return JsonResponse({"message":"crafted"})
    elif request.method == "PUT":
        component_id = int(request.body)
        component = Component.objects.get(pk=component_id)
        component.inventory += 1
        component.save()
        for ci in component.componentingredient_set.all():
            ci.ingredient.inventory -= ci.quantity
            ci.ingredient.save()
        return JsonResponse({"message":"crafted"})

@login_required
def component_availability(request):
    if request.method == "GET":
        components = Component.objects.all()
        dishes = Dish.objects.all()
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
            component.save()
        else:
            dish = Dish.objects.get(pk=body["id"])
            dish.in_stock = not dish.in_stock
            dish.save()
        return JsonResponse({"message":"Toggled availability"})
    

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