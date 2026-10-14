"""
manage.py seed_recipes: add the Cookery mockup's recipes (docs/mockups/cookery) to any database, to try Cookery out.

Safe to run on a database with real data, and again: it reuses ingredients and components that already exist (by
title), only gives a recipe to a component that has none, and skips transforms that are already there. Nothing is
deleted or overwritten. Amounts are in the smallest units (g, ml, pieces).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from inventory.models import Recipe, RecipeIngredient, RecipeStep, Transformation
from pos_server.models import Component, ComponentIngredient, Ingredient

# title: (unit, stock to start with)
INGREDIENTS = {
    "Flour": ("g", 12000), "Eggs": ("ea", 60), "Potatoes": ("g", 8000), "Farmer's cheese": ("g", 3000),
    "Yellow onions": ("g", 9000), "Butter": ("g", 4000), "Kosher salt": ("g", 5000), "Ground beef": ("g", 9000),
    "Black pepper": ("g", 400), "Beef bones": ("g", 5000), "Carrots": ("g", 4000), "Celery": ("g", 2000),
    "Bay leaves": ("ea", 30), "Milk": ("ml", 8000), "Sugar": ("g", 5000), "Instant yeast": ("g", 300),
    "Mayonnaise": ("g", 4000), "Dill pickles": ("g", 2000), "Yellow mustard": ("g", 1500), "Smoked paprika": ("g", 200),
}

# component: (unit, stock, par, makes per batch, [(ingredient, amount per batch)], [(title, text, minutes, scene, [uses])])
RECIPES = {
    "Pierogi, potato & cheese": ("ea", 24, 120, 60, [
        ("Flour", 1200), ("Eggs", 3), ("Potatoes", 1500), ("Farmer's cheese", 600), ("Yellow onions", 400), ("Butter", 100), ("Kosher salt", 20),
    ], [
        ("Boil the potatoes", "Peel and quarter the potatoes. Cover with cold salted water and boil until a knife slides in easily.", 20, "boil", ["Potatoes", "Kosher salt"]),
        ("Fry the onion", "Dice the onion and cook it in the butter over medium heat until soft and golden.", 10, "fry", ["Yellow onions", "Butter"]),
        ("Make the filling", "Drain the potatoes, mash them with the cheese and onion, season to taste, then spread on a tray to cool.", 15, "chill", ["Farmer's cheese"]),
        ("Make the dough", "Mix the flour, eggs, a pinch of salt and about 450 ml warm water. Knead until smooth, then cover and let it rest.", 30, "rest", ["Flour", "Eggs"]),
        ("Roll and cut", "Roll the dough to about 2 mm and cut 7 cm rounds. Gather the scraps and roll them once more.", 0, "prep", []),
        ("Fill and pinch", "Put a heaped teaspoon of filling on each round, fold and pinch the edges tight. Count them onto trays as you go.", 0, "prep", []),
    ]),
    "Beef stock": ("ml", 2500, 12000, 10000, [
        ("Beef bones", 6000), ("Carrots", 1000), ("Celery", 500), ("Yellow onions", 1000), ("Bay leaves", 4),
    ], [
        ("Roast the bones", "Spread the bones on sheet trays and roast at 220 °C until deep brown.", 45, "bake", ["Beef bones"]),
        ("Bring to a boil", "Move bones and vegetables to the stock pot, cover with 14 L cold water and bring to a boil. Skim the foam.", 15, "boil", ["Carrots", "Celery", "Yellow onions", "Bay leaves"]),
        ("Simmer low", "Turn it down to a lazy simmer, uncovered. Skim every half hour and top up with water if needed.", 240, "simmer", []),
        ("Strain and chill", "Strain through a fine sieve into tubs and chill fast in an ice bath before the walk-in.", 60, "chill", []),
    ]),
    "Brioche buns, house-made": ("ea", 26, 60, 24, [
        ("Flour", 1000), ("Milk", 250), ("Eggs", 5), ("Butter", 250), ("Sugar", 80), ("Instant yeast", 14), ("Kosher salt", 15),
    ], [
        ("Mix and knead", "Mix everything but the butter, knead 5 minutes, then work the soft butter in a little at a time.", 10, "prep",
         ["Flour", "Milk", "Eggs", "Butter", "Sugar", "Instant yeast", "Kosher salt"]),
        ("First proof", "Cover the bowl and let the dough rise somewhere warm until doubled.", 60, "rise", []),
        ("Shape the buns", "Divide into 24 pieces of about 70 g, roll tight balls and set them on lined trays.", 0, "prep", []),
        ("Second proof", "Cover loosely and let the buns puff up until they spring back slowly.", 45, "rise", []),
        ("Egg wash and bake", "Brush with egg wash and bake at 190 °C until deep golden.", 18, "bake", []),
    ]),
    "Smash sauce": ("ml", 120, 2400, 2400, [
        ("Mayonnaise", 1500), ("Dill pickles", 300), ("Yellow mustard", 150), ("Smoked paprika", 10),
    ], [
        ("Chop the pickles", "Chop the pickles fine and squeeze out the brine.", 0, "prep", ["Dill pickles"]),
        ("Mix", "Whisk the mayo, mustard and paprika, then fold in the pickles.", 0, "prep", ["Mayonnaise", "Yellow mustard", "Smoked paprika"]),
        ("Portion", "Fill the 2 oz cups, lid them and date them.", 0, "prep", []),
    ]),
    "Caramelized onion": ("g", 800, 3000, 2000, [
        ("Yellow onions", 6000), ("Butter", 200), ("Kosher salt", 20),
    ], [
        ("Slice the onions", "Halve and slice the onions thin, with the grain.", 0, "prep", ["Yellow onions"]),
        ("Cook low and slow", "Melt the butter in the wide rondeau, add onions and salt, and stir every few minutes until deep brown and jammy.", 45, "fry", ["Butter", "Kosher salt"]),
        ("Cool and tub", "Spread on a tray to cool, then pack into tubs.", 10, "chill", []),
    ]),
}

HOW = {
    "freeze": "Lay them flat on parchment-lined trays in the blast freezer. Bag them once they are hard.",
    "thaw": "Move them to the walk-in on a tray, single layer. Ready when soft all the way through. Never thaw at room temperature.",
}


class Command(BaseCommand):
    help = "Add the Cookery mockup's recipes, frozen versions and freeze/thaw transforms (safe to run on real data)."

    def handle(self, *args, **options):
        added = []
        with transaction.atomic():
            ingredients = {}
            for title, (unit, stock) in INGREDIENTS.items():
                ingredients[title], created = Ingredient.objects.get_or_create(
                    title=title, defaults={"unit_of_measurement": unit, "inventory": stock})
                if created:
                    added.append(f"ingredient {title}")

            def component(title, unit, stock, par, option="craft"):
                obj, created = Component.objects.get_or_create(title=title, defaults={
                    "unit_of_measurement": unit, "type": "food", "inventory": stock, "par": par, "crafting_option": option})
                if created:
                    added.append(f"component {title}")
                elif obj.par is None:
                    Component.objects.filter(pk=obj.pk).update(par=par)
                return obj

            made = {}
            for title, (unit, stock, par, makes, parts, steps) in RECIPES.items():
                obj = made[title] = component(title, unit, stock, par)
                if obj.recipe_id:
                    continue  # it has a recipe already; leave it alone
                if obj.unit_of_measurement != unit:
                    self.stdout.write(self.style.WARNING(f"Skipped the recipe for {title}: it's counted in {obj.unit_of_measurement}, not {unit}"))
                    continue
                recipe = Recipe.objects.create(yield_quantity=makes)
                rows = {name: RecipeIngredient.objects.create(recipe=recipe, ingredient=ingredients[name], quantity=qty, position=n)
                        for n, (name, qty) in enumerate(parts)}
                for n, (step_title, text, minutes, scene, uses) in enumerate(steps):
                    step = RecipeStep.objects.create(recipe=recipe, position=n, title=step_title, text=text, scene=scene,
                                                     duration=timedelta(minutes=minutes) if minutes else None)
                    step.uses.set([rows[name] for name in uses])
                Component.objects.filter(pk=obj.pk).update(recipe=recipe, crafting_option="craft")
                added.append(f"recipe for {title}")

            # Burger patties are made one at a time (no recipe): reuse a "Beef patty" if there is one
            patty = Component.objects.filter(title="Beef patty").first()
            if patty is None:
                patty = component("Beef patty", "ea", 10, 80)
                for name, qty in (("Ground beef", 120), ("Kosher salt", 1), ("Black pepper", 0.5)):
                    ComponentIngredient.objects.create(component=patty, ingredient=ingredients[name], quantity=qty)
            frozen_pierogi = component("Pierogi, frozen", "ea", 180, 200, option="transform")
            frozen_patty = component("Beef patty, frozen", "ea", 40, 80, option="transform")
            pierogi = made["Pierogi, potato & cheese"]
            for name, source, target, scene, minutes in (
                ("Freeze pierogi", pierogi, frozen_pierogi, "freeze", 120),
                ("Thaw pierogi", frozen_pierogi, pierogi, "thaw", 480),
                ("Freeze patties", patty, frozen_patty, "freeze", 90),
                ("Thaw patties", frozen_patty, patty, "thaw", 720),
            ):
                if not Transformation.objects.filter(name=name).exists():
                    Transformation.objects.create(name=name, from_component=source, to_component=target, scene=scene,
                                                  duration=timedelta(minutes=minutes), instructions=HOW[scene])
                    added.append(f"transform {name}")
        if added:
            self.stdout.write(self.style.SUCCESS("Added: " + ", ".join(added)))
        else:
            self.stdout.write("Nothing to add: it's all there already.")
