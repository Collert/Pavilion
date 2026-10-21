"""
What a dish is made of, worked out from its recipe: food cost, allergens and the diets it fits.

A dish uses DishComponent.quantity of each component; a component uses ComponentIngredient.quantity of each
ingredient per unit (both in the smallest unit). Prefetch `dishcomponent_set__component__componentingredient_set__ingredient`
(DISH_PREFETCH) before calling these on many dishes.
"""
from decimal import Decimal

from .models import ALLERGENS, DIETS

DISH_PREFETCH = "dishcomponent_set__component__componentingredient_set__ingredient"


def component_cost(component) -> Decimal:
    """Cost of one unit of a component (a gram, a millilitre or a piece) from its ingredients' costs."""
    return sum((Decimal(str(ci.quantity)) * (ci.ingredient.cost or 0) for ci in component.componentingredient_set.all()), Decimal("0"))


def plate_cost(dish) -> Decimal | None:
    """What the components of one standard portion cost, or None when the dish has no recipe."""
    parts = list(dish.dishcomponent_set.all())
    if not parts:
        return None
    return sum((Decimal(str(dc.quantity)) * component_cost(dc.component) for dc in parts), Decimal("0"))


def food_cost_pct(cost, price) -> float | None:
    """Cost as a share of the price (30.4), or None when there's no recipe or price."""
    if cost is None or not price:
        return None
    return round(float(cost / Decimal(price) * 100), 1)


def ingredients_of(dish):
    return [ci.ingredient for dc in dish.dishcomponent_set.all() for ci in dc.component.componentingredient_set.all()]


def dish_allergens(dish) -> list[str]:
    """The allergens of every ingredient in the dish, in the usual order."""
    found = {a for ingredient in ingredients_of(dish) for a in (ingredient.allergens or [])}
    return [a for a in ALLERGENS if a in found]


def dish_diets(dish) -> list[dict]:
    """The diets every ingredient fits ({key, label, short}). A dish with no recipe fits none (we can't tell)."""
    ingredients = ingredients_of(dish)
    if not dish.dishcomponent_set.all():
        return []
    flags = [i.flags for i in ingredients]
    combined = {k: any(f[k] for f in flags) for k in ("meat", "fish", "animal", "gluten")}
    return [{"key": key, "label": label, "short": short} for key, label, short, test in DIETS if test(combined)]
