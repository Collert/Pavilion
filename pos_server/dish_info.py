"""
What a dish is made of, worked out from its recipe: food cost, allergens and the diets it fits.

A dish uses DishComponent.quantity of each component (in the smallest unit). A component's cost and ingredients come
from, in order: its prep recipe (one batch's ingredients and components over the batch's yield), its per-unit
ComponentIngredients, or the component a Cookery transformation makes it from (frozen patties cost what fresh ones
do). Costs always come from ingredient prices (Ingredient.cost, from its pack price), never set on a component. The `parts_*` functions take
[(component, quantity)], so the admin's dish editor can show them for a recipe that isn't saved yet. Prefetch
DISH_PREFETCH before calling these on many dishes.
"""
from decimal import Decimal

from .models import ALLERGENS, DIETS

COMPONENT_PREFETCH = ["componentingredient_set__ingredient", "recipe__ingredients__ingredient", "recipe__ingredients__component"]
DISH_PREFETCH = [f"dishcomponent_set__component__{path}" for path in COMPONENT_PREFETCH]
MAX_DEPTH = 4  # recipes can use components that have recipes; stop at a loop


def parts_of(dish):
    return [(dc.component, dc.quantity) for dc in dish.dishcomponent_set.all()]


def recipe_rows(component):
    """The rows of the component's prep recipe when it has a usable one (a yield and something in it), else None."""
    recipe = component.recipe if component.recipe_id else None
    if recipe is None or not recipe.yield_quantity or recipe.yield_quantity <= 0:
        return None
    rows = list(recipe.ingredients.all())
    return rows or None


def made_from(component):
    """The Transformation that makes this component from another one (thawing frozen patties), if any."""
    if not component.pk:
        return None
    from inventory.models import Transformation  # inventory imports pos_server.models
    return Transformation.objects.filter(to_component=component).exclude(from_component=component).select_related("from_component").first()


def component_cost(component, _depth=0) -> Decimal:
    """Cost of one unit of a component (a gram, a millilitre or a piece)."""
    if _depth > MAX_DEPTH:
        return Decimal("0")
    rows = recipe_rows(component)
    if rows:
        batch = sum((Decimal(str(row.quantity)) * ((row.ingredient.cost or 0) if row.ingredient_id else component_cost(row.component, _depth + 1))
                     for row in rows), Decimal("0"))
        return batch / Decimal(str(component.recipe.yield_quantity))
    per_unit = component.componentingredient_set.all() if component.pk else []  # (an unsaved one has none)
    if per_unit:
        return sum((Decimal(str(ci.quantity)) * (ci.ingredient.cost or 0) for ci in per_unit), Decimal("0"))
    transform = made_from(component)
    if transform:
        return component_cost(transform.from_component, _depth + 1) / Decimal(str(transform.ratio or 1))
    return Decimal("0")


def component_ingredients(component, _depth=0) -> list:
    """Every ingredient that goes into a component (through its recipe's components too)."""
    if _depth > MAX_DEPTH:
        return []
    rows = recipe_rows(component)
    if rows:
        out = []
        for row in rows:
            out += [row.ingredient] if row.ingredient_id else component_ingredients(row.component, _depth + 1)
        return out
    per_unit = [ci.ingredient for ci in component.componentingredient_set.all()] if component.pk else []
    if per_unit:
        return per_unit
    transform = made_from(component)
    return component_ingredients(transform.from_component, _depth + 1) if transform else []


def component_allergens(component) -> list[str]:
    found = {a for ingredient in component_ingredients(component) for a in (ingredient.allergens or [])}
    return [a for a in ALLERGENS if a in found]


def parts_cost(parts) -> Decimal | None:
    """What the components of one standard portion cost, or None when there's no recipe."""
    if not parts:
        return None
    return sum((Decimal(str(quantity)) * component_cost(component) for component, quantity in parts), Decimal("0"))


def plate_cost(dish) -> Decimal | None:
    return parts_cost(parts_of(dish))


def food_cost_pct(cost, price) -> float | None:
    """Cost as a share of the price (30.4), or None when there's no recipe or price."""
    if cost is None or not price:
        return None
    return round(float(cost / Decimal(price) * 100), 1)


def _ingredients(parts):
    return [ingredient for component, _quantity in parts for ingredient in component_ingredients(component)]


def parts_allergens(parts) -> list[str]:
    """The allergens of every ingredient, in the usual order."""
    found = {a for ingredient in _ingredients(parts) for a in (ingredient.allergens or [])}
    return [a for a in ALLERGENS if a in found]


def parts_diets(parts) -> list[dict]:
    """The diets every ingredient fits ({key, label, short}). No recipe fits none (we can't tell)."""
    if not parts:
        return []
    flags = [i.flags for i in _ingredients(parts)]
    combined = {k: any(f[k] for f in flags) for k in ("meat", "fish", "animal", "gluten")}
    return [{"key": key, "label": label, "short": short} for key, label, short, test in DIETS if test(combined)]


def dish_allergens(dish) -> list[str]:
    return parts_allergens(parts_of(dish))


def dish_diets(dish) -> list[dict]:
    return parts_diets(parts_of(dish))
