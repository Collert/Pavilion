from django.shortcuts import render
from pos_server.models import *
from collections import defaultdict
from pos_server.views import prettify_dish

# Create your views here.

def menu(request):
    query = request.GET.get('actual', None)
    if query:
        try:
            active = Menu.objects.get(pk=query)
        except:
            active = Menu.objects.filter(is_active = True).first()
    else:
        active = Menu.objects.filter(is_active = True).first()
    active_dishes = Dish.objects.filter(menu=active)
    grouped_active = defaultdict(list)
    for dish in active_dishes:
        grouped_active[dish.station].append(dish)
    others = Menu.objects.filter(is_active = False).all()
    grouped_other_menus = defaultdict(dict)
    for menu_obj in others:
        this_grouped = defaultdict(list)
        this_dishes = Dish.objects.filter(menu=menu_obj)
        for dish in this_dishes:
            this_grouped[dish.station].append(dish)
        grouped_other_menus[menu_obj.title] = dict(this_grouped)
    return render(request, "online_store/menu.html", {
        "route":"menu",
        "menu":{"menu":active, "dishes":dict(grouped_active)},
        "others":dict(grouped_other_menus),
        "override_menu": query
    })

def dish(request, id):
    item = Dish.objects.get(pk=id)
    allergens = set()
    for dc in item.dishcomponent_set.all():
        for ci in dc.component.componentingredient_set.all():
            allergens.add(ci.ingredient.allergen)
    if len(allergens) > 1:
        allergens.remove(None)
    print(allergens)
    return render(request, "online_store/dish.html", {
        "route":"dish",
        "dish":item,
        "pretty_dish":prettify_dish(item),
        "menu":{"menu":item.menu.first()},
        "allergens":', '.join(allergens) if None not in allergens else None
    })
    