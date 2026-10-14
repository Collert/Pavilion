from django.shortcuts import render
from pos_server.models import *
from .models import *
from django.core import serializers
from django.db import transaction
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect, JsonResponse
from django.urls import reverse
import json
import time
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.views.decorators import staff_member_required

from pos_server.devices import api_staff_required, staff_required
from .availability import apply_changes, catalog

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
            "json": json.loads(serializers.serialize('json', ingredients)),
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

@staff_required
def component_availability(request):
    """The Availability screen (docs/mockups/Availability-html). The panel itself is availability_panel.html."""
    return render(request, "inventory/availability.html", {"nav": "inventory"})


@api_staff_required
def availability_data(request):
    """
    The Availability panel's data. GET: inventory.availability.catalog(). POST {"changes": [{kind, id, mode}]}:
    set those modes (all or nothing), then return the catalog again.
    """
    if request.method == "POST":
        try:
            with transaction.atomic():
                apply_changes(json.loads(request.body).get("changes"))
        except (ValueError, AttributeError) as e:
            return JsonResponse({"error": str(e)}, status=400)
    elif request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])
    return JsonResponse(catalog())

def craft_component(component_id:int, qty:int):
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