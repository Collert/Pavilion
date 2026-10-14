from django.conf import settings
from django.db import models
from django.utils import timezone

# Create your models here.

class StockUpdate(models.Model):
    """
    Model representing a stock update.

    Attributes:
        date (DateTimeField): The date and time when the stock update was created. Automatically set to the current date and time when the object is created.
        receipt (FileField): An optional file field to upload a receipt for the stock update. The file will be uploaded to the 'files/ingredient_receipts' directory.
    """
    date = models.DateTimeField(auto_now_add = True)
    receipt = models.FileField(upload_to='files/ingredient_receipts', null=True)

class UpdateIngredient(models.Model):
    """
    Model representing an update to an ingredient's stock.

    Attributes:
        update_obj (ForeignKey): Reference to the StockUpdate model. When the referenced StockUpdate is deleted, this update will also be deleted.
        ingredient (ForeignKey): Reference to the Ingredient model in the pos_server app. When the referenced Ingredient is deleted, this update will also be deleted.
        quantity (IntegerField): The quantity of the ingredient being updated. Defaults to 1.
    """
    update_obj = models.ForeignKey("StockUpdate", on_delete = models.CASCADE)
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE)
    quantity = models.IntegerField(default=1)

# What a timer or transform looks like in Cookery (the animated scene; see inventory/static/inventory/cook-scene.js)
SCENES = (
    ("prep", "Prep work"),
    ("boil", "Boil"),
    ("simmer", "Simmer"),
    ("fry", "Fry"),
    ("bake", "Bake"),
    ("rise", "Rise / proof"),
    ("rest", "Rest"),
    ("chill", "Chill"),
    ("freeze", "Freeze"),
    ("thaw", "Thaw"),
)


class Recipe(models.Model):
    """
    How to make a batch of a component (or a dish): the ingredients for one batch, the steps, and about how much
    one batch makes (`yield_quantity`, in the component's unit). Cookery takes the ingredients out of inventory when
    a batch starts and adds what was actually counted when it's finished. Components link to it with
    Component.recipe (dishes with Dish.recipe). Edited in /advanced-admin/.
    """
    yield_quantity = models.FloatField(default=1, help_text="About how much one batch makes, in the component's unit")
    notes = models.TextField(blank=True, help_text="Anything else worth knowing (not shown step by step)")

    def __str__(self) -> str:
        owner = self.component.first() or self.dish.first()
        return f"Recipe for {owner.title}" if owner else f"Recipe #{self.pk}"

    @property
    def total_seconds(self) -> int:
        return sum(step.duration_seconds for step in self.steps.all())


class RecipeIngredient(models.Model):
    """An ingredient and how much of it one batch of the recipe takes (in the ingredient's unit)."""
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ingredients")
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete=models.PROTECT, related_name="recipe_uses")
    quantity = models.FloatField(help_text="For one batch, in the ingredient's unit")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.quantity} {self.ingredient.unit_of_measurement} {self.ingredient.title}"


class RecipeStep(models.Model):
    """One step of a recipe. Steps with a duration get a timer in guided cooking."""
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="steps")
    position = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=80)
    text = models.TextField(blank=True)
    duration = models.DurationField(null=True, blank=True, help_text="Timer length, e.g. 00:20:00. Leave empty for no timer")
    scene = models.CharField(max_length=10, choices=SCENES, default="prep", help_text="The picture shown while it runs")
    uses = models.ManyToManyField(RecipeIngredient, blank=True, related_name="steps",
                                  help_text="Ingredients this step needs (shown with their amounts)")

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.title

    @property
    def duration_seconds(self) -> int:
        return int(self.duration.total_seconds()) if self.duration else 0


class Transformation(models.Model):
    """
    Turning one component into another over time, e.g. freezing pierogi (fresh -> frozen) or thawing them back.
    `quantity` of `from_component` leaves inventory when it starts, and `quantity * ratio` of `to_component`
    arrives when it's marked done.
    """
    name = models.CharField(max_length=80, help_text="e.g. 'Freeze pierogi'")
    from_component = models.ForeignKey("pos_server.Component", on_delete=models.CASCADE, related_name="transforms_from")
    to_component = models.ForeignKey("pos_server.Component", on_delete=models.CASCADE, related_name="transforms_to")
    ratio = models.FloatField(default=1, help_text="How much comes out for each one that goes in")
    scene = models.CharField(max_length=10, choices=SCENES, default="freeze")
    duration = models.DurationField(help_text="About how long it takes, e.g. 02:00:00")
    instructions = models.TextField(blank=True, help_text="How to do it")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.name


class CookingSession(models.Model):
    """
    A batch in progress in Cookery, shared by everyone on shift: cooking a component (guided through its recipe's
    steps or straight to counting) or running a transformation. `holder` is whoever has it now; anyone can pick it up.
    `timers` holds the running step timers: {"<step>": {"end": iso or null, "dur": seconds, "paused": seconds or null}}.
    """
    COOK = "cook"
    TRANSFORM = "transform"
    kind = models.CharField(max_length=10, choices=((COOK, "Cooking"), (TRANSFORM, "Transform")))
    component = models.ForeignKey("pos_server.Component", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    recipe = models.ForeignKey(Recipe, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    batch = models.PositiveSmallIntegerField(default=1)
    transformation = models.ForeignKey(Transformation, on_delete=models.CASCADE, null=True, blank=True, related_name="sessions")
    quantity = models.FloatField(default=0, help_text="Transforms: how much went in")
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    holder = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    started_at = models.DateTimeField(default=timezone.now)
    phase = models.CharField(max_length=10, default="guided", choices=(("guided", "Guided"), ("yield", "Counting")))
    step = models.PositiveSmallIntegerField(default=0)
    timers = models.JSONField(default=dict, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Transforms: when it should be ready")
    finished_at = models.DateTimeField(null=True, blank=True)
    cancelled = models.BooleanField(default=False)
    made = models.FloatField(null=True, blank=True, help_text="What was counted at the end")
    expected = models.FloatField(null=True, blank=True, help_text="What the recipe said a batch makes")

    class Meta:
        ordering = ["started_at"]

    def __str__(self):
        what = self.transformation or self.component
        return f"{self.get_kind_display()}: {what}"


class YieldEntry(models.Model):
    """One amount counted while recording a batch's yield (undo removes the latest)."""
    session = models.ForeignKey(CookingSession, on_delete=models.CASCADE, related_name="log")
    amount = models.FloatField()
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "id"]


class Packaging(models.Model):
    """
    Represents a packaging in the inventory system.
    Attributes:
        name (CharField): The name of the packaging.
        quantity (PositiveIntegerField): The quantity of the packaging.
    """
    name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    cost = models.DecimalField(max_digits=10, decimal_places=2)

class CondimentUse(models.Model):
    """
    Represents a condiment usage entry. Used to adjust waste reports.
    Attributes:
        date (DateTimeField): The date and time when the condiment was used. Automatically set to the current date and time when the object is created.
        condiment (ForeignKey): Reference to the Ingredient model in the pos_server app. When the referenced Ingredient is deleted, this usage entry will also be deleted.
        quantity (PositiveIntegerField): The quantity of the condiment used.
    """
    date = models.DateTimeField(auto_now_add = True)
    condiment = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE)
    quantity = models.PositiveIntegerField()
    
class IngredientUsageBuffer(models.Model):
    # TODO: Add functionality to mark out ingredients which will add the quantity to the buffer and subtract from the stock.
    # Later, the buffer can be used in crafting and serve as a comparison between the expected and actual usage.
    # At the end of the day, convert the buffer to a waste entry.
    """
    Represents a used unaccounted for ingredient.
    Attributes:
        recipe (ForeignKey): Reference to the Recipe model. When the referenced Recipe is deleted, this buffer will also be deleted.
        ingredient (ForeignKey): Reference to the Ingredient model in the pos_server app. When the referenced Ingredient is deleted, this buffer will also be deleted.
        quantity (PositiveIntegerField): The quantity of the ingredient used in the recipe.
    """
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE, related_name="usage_buffer")
    usage = models.PositiveIntegerField()

class WasteIngredient(models.Model):
    """
    Represents a waste entry for ingredients.
    Attributes:
        date (DateTimeField): The date and time when the waste entry was created. Automatically set to the current date and time when the object is created.
        ingredient (ForeignKey): Reference to the Ingredient model in the pos_server app. When the referenced Ingredient is deleted, this waste entry will also be deleted.
        quantity (PositiveIntegerField): The quantity of the ingredient wasted.
    """
    date = models.DateTimeField(auto_now_add = True)
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE)
    quantity = models.PositiveIntegerField()

class WasteComponent(models.Model):
    """
    Represents a waste entry for components.
    Attributes:
        date (DateTimeField): The date and time when the waste entry was created. Automatically set to the current date and time when the object is created.
        component (ForeignKey): Reference to the Component model in the pos_server app. When the referenced Component is deleted, this waste entry will also be deleted.
        quantity (PositiveIntegerField): The quantity of the component wasted.
    """
    date = models.DateTimeField(auto_now_add = True)
    component = models.ForeignKey("pos_server.Component", on_delete = models.CASCADE)
    quantity = models.PositiveIntegerField()

class WastePackaging(models.Model):
    """
    Represents a waste entry for packaging.
    Attributes:
        date (DateTimeField): The date and time when the waste entry was created. Automatically set to the current date and time when the object is created.
        packaging (ForeignKey): Reference to the Packaging model. When the referenced Packaging is deleted, this waste entry will also be deleted.
        quantity (PositiveIntegerField): The quantity of the packaging wasted.
    """
    date = models.DateTimeField(auto_now_add = True)
    packaging = models.ForeignKey("Packaging", on_delete = models.CASCADE)
    quantity = models.PositiveIntegerField()