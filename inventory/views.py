from django.shortcuts import render
from pos_server.models import *
from .models import *
from django.core import serializers
from django.http import HttpResponseRedirect, StreamingHttpResponse, JsonResponse
from django.urls import reverse
import json
from . import globals
import time
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.views.decorators import staff_member_required
import markdown

# Create your views here.

@login_required
@user_passes_test(lambda u: u.is_superuser)
def log_shopping(request):
    """
    Handle the logging of shopping activities.

    This view supports both GET and POST requests:
    - GET: Renders the shopping log page with a list of ingredients.
    - POST: Processes the shopping log form submission, updates inventory, and saves the receipt.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered shopping log page for GET requests.
        HttpResponseRedirect: Redirects to the shopping log page after processing POST requests.
    """
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
@user_passes_test(lambda u: u.is_superuser)
def shopping_history(request):
    """
    View function to display the shopping history.

    This function retrieves all instances of StockUpdate and renders the 
    'inventory/shopping-history.html' template with the shopping history data.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered HTML page displaying the shopping history.
    """
    shopping_occasions = StockUpdate.objects.all()
    return render(request, "inventory/shopping-history.html", {
        "history":shopping_occasions,
        "route":"shopping_history"
    })

@login_required
@user_passes_test(lambda u: u.is_superuser)
def day_display(request, day_id):
    day = StockUpdate.objects.get(id=day_id)
    return render(request, "inventory/day-display.html", {
        "day":day
    })

@login_required
def crafting(request):
    """
    Handle crafting-related requests.

    This view function handles GET, POST, and PUT requests for crafting components.

    GET:
        - Retrieves components with the crafting option set to "craft".
        - Retrieves all ingredients.
        - Renders the "inventory/crafting.html" template with the following context:
            - route: "crafting"
            - components: List of components with crafting option "craft"
            - ingredients: List of all ingredients
            - ingredient_inventory: Inventory of ingredients collected by `collect_ing_stock`

    POST:
        - Parses the request body as JSON.
        - Iterates over the component IDs in the request body.
        - Crafts the specified quantity of each component.
        - Returns a JSON response with a success message.

    PUT:
        - Parses the request body to get the component ID.
        - Crafts one unit of the specified component.
        - Returns a JSON response with a success message.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The HTTP response object.
    """
    if request.method == "GET":
        components = Component.objects.filter(crafting_option="craft")
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
    """
    Handle the availability of components and dishes in the inventory.

    This view handles both GET and PUT requests:
    - GET: Renders the component availability page with the list of components and dishes.
    - PUT: Toggles the in_stock status of a component or dish based on the request body.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered component availability page for GET requests.
        JsonResponse: A JSON response with the updated list of dishes for PUT requests.
    """
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
    """
    Handle the recipes view for GET and POST requests.

    GET:
    - If the request method is GET, it attempts to retrieve and parse the 'edit' parameter from the request.
    - Based on the 'edit' parameter, it fetches the corresponding Dish or Component object and extracts recipe steps.
    - Renders the 'inventory/recipes.html' template with the list of recipes, components, dishes, and editing context.

    POST:
    - If the request method is POST, it processes the form data to create a new recipe.
    - Retrieves the item type (dish or component) and its ID from the form data.
    - Constructs the markdown text for the recipe steps.
    - Creates a new Recipe object and associates it with the corresponding Dish or Component.
    - Redirects to the 'recipes' view after saving the new recipe.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The rendered HTML response for GET requests.
        HttpResponseRedirect: A redirect response to the 'recipes' view for POST requests.
    """
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
    """
    Handles the recipe view for a given recipe ID.

    Args:
        request (HttpRequest): The HTTP request object.
        recipe_id (int): The ID of the recipe to be displayed.

    Returns:
        HttpResponse: The rendered HTML page displaying the recipe details.

    This view handles GET requests to display the details of a recipe. It retrieves the recipe
    object from the database using the provided recipe ID. Depending on whether the recipe is
    a dish or a component, it generates an HTML unordered list of ingredients with their
    quantities adjusted by the original yield of the recipe. The view then renders the
    "inventory/recipe.html" template with the recipe object, the generated ingredients list,
    the type of the recipe (dish or component), the converted markdown text of the recipe,
    and the route name.
    """
    if request.method == "GET":
        md = markdown.Markdown(extensions=['tables'])
        recipe = Recipe.objects.get(pk=recipe_id)
        recipe_obj_type = "dish" if recipe.dish.first() else "component"
        ingredients_ul = "<ul>"
        if recipe_obj_type == "dish":
            for dc in recipe.dish.first().dishcomponent_set.all():
                ingredients_ul += f"<li>{str(round(dc.quantity * recipe.original_yield, 2))} {dc.component.unit_of_measurement.capitalize()} {dc.component.title}</li>"
        else:
            for ci in recipe.component.first().componentingredient_set.all():
                ingredients_ul += f"<li>{str(round(ci.quantity * recipe.original_yield, 2))} {ci.ingredient.unit_of_measurement.capitalize()} {ci.ingredient.title}</li>"
        ingredients_ul += "</ul>"
        print(ingredients_ul)
        return render(request, "inventory/recipe.html", {
            "recipe_obj":recipe,
            "ingredients_ul":ingredients_ul,
            "recipe_obj_type":recipe_obj_type,
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
        yield ":heartbeat\n\n"
        time.sleep(1)

def collect_ing_stock(ingredients):
    """
    Collects the inventory stock for a list of ingredients and returns it as a JSON string.

    Args:
        ingredients (list): A list of ingredient objects, each with an 'id' and 'inventory' attribute.

    Returns:
        str: A JSON string representing the inventory stock of the ingredients, 
             where the keys are ingredient IDs and the values are their respective inventory counts.
    """
    inventory = {}
    for ingredient in ingredients:
        inventory[f"{ingredient.id}"] = ingredient.inventory
    return json.dumps(inventory)

def inventory_updates(request):
    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    # response['Cache-Control'] = 'no-cache'
    return response

def craft_component(component_id:int, qty:int):
    def craft_component(component_id: int, qty: int):
        """
        Updates the inventory of a component and its ingredients based on the quantity crafted.

        Args:
            component_id (int): The ID of the component to be crafted.
            qty (int): The quantity of the component to be crafted.

        Raises:
            Component.DoesNotExist: If the component with the given ID does not exist.

        Side Effects:
            - Increases the inventory of the specified component by the given quantity.
            - Decreases the inventory of each ingredient used in the component by the required amount, 
              unless the ingredient has unlimited supply.
        """
    component = Component.objects.get(pk=component_id)
    component.inventory += qty
    component.save()
    for ci in component.componentingredient_set.all():
        if not ci.ingredient.unlimited:
            ci.ingredient.inventory -= ci.quantity * qty
            ci.ingredient.save()