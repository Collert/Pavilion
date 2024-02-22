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
from django.contrib.admin.views.decorators import staff_member_required
import markdown

# Create your views here.

@login_required
def log_shopping(request):
    if request.method == "GET":
        ingredients = Ingredient.objects.filter(unlimited=False)
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
    
@staff_member_required
def recipes(request):
    if request.method == "GET":
        try:
            type = request.GET['edit'].split("-")[0]
            id = request.GET['edit'].split("-")[1]
            obj = Dish.objects.get(pk=int(id)) if type == "d" else Component.objects.get(pk=int(id))
            steps = obj.recipe.markdown_text.split("|")[8: -1: 3]
            editing = (type, id, obj, obj.recipe, steps)
        except:
            editing = None
        objs = Recipe.objects.all()
        components = Component.objects.filter(recipe=None).all()
        dishes = Dish.objects.filter(recipe=None).all()
        return render(request, "inventory/recipes.html", {
            "recipes":objs,
            "components":components,
            "dishes":dishes,
            "editing":editing,
            "route":"recipes"
        })
    elif request.method == "POST":
        qty_steps = int(request.POST["qty-steps"])
        item_type = request.POST["item"].split("-")[0]
        try:
            recipe_yield = int(request.POST['default-yield'])
        except ValueError:
            recipe_yield = 1
        method_md = ""
        if item_type == "dish":
            item = Dish.objects.get(pk=request.POST["item"].split("-")[1])
        else:
            item = Component.objects.get(pk=request.POST["item"].split("-")[1])
        method_md += "|||\n|-|-|\n"
        empty_steps = 0
        for index in range(qty_steps):
            if request.POST[f'step-{index + 1}']:
                method_md += f"|Step {index + 1 - empty_steps}:|{request.POST[f'step-{index + 1}']}|\n"
            else:
                empty_steps += 1
        new_recipe = Recipe.objects.create(markdown_text=method_md, original_yield=recipe_yield)
        item.recipe = new_recipe
        item.save()
        return HttpResponseRedirect(reverse("recipes"))

@staff_member_required
def recipe(request, recipe_id):
    if request.method == "GET":
        md = markdown.Markdown(extensions=['tables'])
        recipe = Recipe.objects.get(pk=recipe_id)
        return render(request, "inventory/recipe.html", {
            "recipe_obj":recipe,
            "recipe_obj_type":"dish" if recipe.dish.first() else "component",
            "recipe_md":md.convert(recipe.markdown_text),
            "route":"recipe"
        })

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
        if not ci.ingredient.unlimited:
            ci.ingredient.inventory -= ci.quantity * qty
            ci.ingredient.save()