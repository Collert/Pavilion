"""
What a dish is made of, worked out from its recipe: food cost, allergens and the diets it fits.

A dish uses DishComponent.quantity of each component; a component uses ComponentIngredient.quantity of each
ingredient per unit (both in the smallest unit). The `parts_*` functions take [(component, quantity)], so the
admin's dish editor can show them for a recipe that isn't saved yet. Prefetch
`dishcomponent_set__component__componentingredient_set__ingredient` (DISH_PREFETCH) before calling these on many dishes.
"""
from decimal import Decimal

from .models import ALLERGENS, DIETS

DISH_PREFETCH = "dishcomponent_set__component__componentingredient_set__ingredient"


def parts_of(dish):
    return [(dc.component, dc.quantity) for dc in dish.dishcomponent_set.all()]


def component_cost(component) -> Decimal:
    """Cost of one unit of a component (a gram, a millilitre or a piece) from its ingredients' costs."""
    return sum((Decimal(str(ci.quantity)) * (ci.ingredient.cost or 0) for ci in component.componentingredient_set.all()), Decimal("0"))


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
    return [ci.ingredient for component, _quantity in parts for ci in component.componentingredient_set.all()]


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
