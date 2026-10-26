from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

# Create your models here.

class StockUpdate(models.Model):
    """
    Model representing a stock update.

    Attributes:
        date (DateTimeField): The date and time when the stock update was created. Automatically set to the current date and time when the object is created.
        receipt (FileField): An optional file field to upload a receipt for the stock update. The file will be uploaded to the 'ingredient_receipts' directory.
    """
    date = models.DateTimeField(auto_now_add = True)
    receipt = models.FileField(upload_to='ingredient_receipts', null=True)

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
    How to make a batch of something: the ingredients (or components) for one batch, the steps, and about how much
    one batch makes (`yield_quantity`, in the target's unit). It's attached to what it makes: a component
    (Component.recipe: Cookery's Craft takes the ingredients out when a batch starts and adds what was counted when
    it's finished), a transformation (Transformation.recipe: its steps, time and batch size in Cookery's Transform) or
    a dish (Dish.recipe: how the plate is put together). `total_minutes` is start to finish, at least the longest
    timer. Edited on the admin's Recipes page.
    """
    title = models.CharField(max_length=140, blank=True)
    yield_quantity = models.FloatField(default=1, help_text="About how much one batch makes, in the target's unit")
    total_minutes = models.PositiveIntegerField(default=0, help_text="Start to finish")
    notes = models.TextField(blank=True, help_text="Anything else worth knowing (not shown step by step)")

    def __str__(self) -> str:
        if self.title:
            return self.title
        owner = self.component.first() or self.dish.first()
        return f"Recipe for {owner.title}" if owner else f"Recipe #{self.pk}"

    def target(self):
        """("component" | "transform" | "dish", the object) this recipe is attached to, or None."""
        for kind, related in (("component", self.component), ("transform", self.transformations), ("dish", self.dish)):
            obj = related.first()
            if obj:
                return kind, obj
        return None

    @property
    def total_seconds(self) -> int:
        return sum(step.duration_seconds for step in self.steps.all())


class RecipeIngredient(models.Model):
    """An ingredient, or a component (pulled pork for a reheat), and how much one batch takes, in its unit."""
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ingredients")
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete=models.PROTECT, related_name="recipe_uses", null=True, blank=True)
    component = models.ForeignKey("pos_server.Component", on_delete=models.PROTECT, related_name="recipe_uses", null=True, blank=True)
    quantity = models.FloatField(help_text="For one batch, in the item's unit")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.CheckConstraint(
            condition=(models.Q(ingredient__isnull=True) & models.Q(component__isnull=False)) |
                      (models.Q(ingredient__isnull=False) & models.Q(component__isnull=True)),
            name="recipe_ingredient_is_one_thing")]

    def __str__(self):
        return f"{self.quantity} {self.unit} {self.item.title}"

    @property
    def item(self):
        return self.ingredient or self.component

    @property
    def unit(self):
        return self.item.unit_of_measurement


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
    recipe = models.ForeignKey(Recipe, on_delete=models.SET_NULL, null=True, blank=True, related_name="transformations",
                               help_text="Its steps, time and batch size (admin Recipes)")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.name

    @property
    def takes(self):
        """How long it takes: its recipe's total time when it has one, else `duration`."""
        if self.recipe_id and self.recipe.total_minutes:
            return timedelta(minutes=self.recipe.total_minutes)
        return self.duration


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


class IngredientReservation(models.Model):
    """
    What a recipe batch took for itself when it started (recipe amount x batch size). It's out of inventory from that
    moment, so no count offers it to anyone else. When the batch finishes short of the recipe's yield, the unused
    share becomes WasteIngredient.
    """
    session = models.ForeignKey(CookingSession, on_delete=models.CASCADE, related_name="reservations")
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete=models.CASCADE, related_name="reservations")
    quantity = models.FloatField()

    def __str__(self):
        return f"{self.quantity} {self.ingredient.unit_of_measurement} {self.ingredient.title} for {self.session}"


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
        quantity (FloatField): The quantity of the ingredient wasted, in its unit.
        session (ForeignKey): The Cookery batch it was reserved for, when it came from one (made less than expected).
    """
    date = models.DateTimeField(auto_now_add = True)
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete = models.CASCADE)
    quantity = models.FloatField()
    session = models.ForeignKey("CookingSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="waste")

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

class Restock(models.Model):
    """
    A purchase order from a supplier (the admin's Restocks page): a draft, then placed (sent), then delivered. When
    it's first saved as delivered, each line's cases are added to its ingredient's stock and its case price becomes
    the ingredient's pack price (so component and dish costs follow); `received_at` records that it happened, and a
    delivered order can't go back. `number` is the order number people use ("PO-148").
    """
    DRAFT, PLACED, DELIVERED = "draft", "placed", "delivered"
    STATUSES = [(DRAFT, "Draft"), (PLACED, "Placed"), (DELIVERED, "Delivered")]

    supplier = models.ForeignKey("pos_server.Supplier", on_delete=models.PROTECT, related_name="restocks")
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    created_on = models.DateField(default=timezone.localdate)
    placed_on = models.DateField(null=True, blank=True)
    expected_on = models.DateField(null=True, blank=True, help_text="Want it by / expected")
    delivered_on = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True, help_text="When its stock and prices were taken in")
    fee = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Delivery fee")
    tax = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Tax on the receipt")
    notes = models.TextField(blank=True)
    receipt = models.FileField(upload_to="restock_receipts", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"PO-{self.id} {self.supplier}"

    @property
    def number(self) -> str:
        return f"PO-{self.id}"

    @property
    def subtotal(self) -> Decimal:
        return sum((line.total for line in self.lines.all()), Decimal("0"))

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.fee + self.tax


class RestockLine(models.Model):
    """Cases of one ingredient on a restock. `was_price` is what a case cost when it was added (last time paid)."""
    restock = models.ForeignKey(Restock, on_delete=models.CASCADE, related_name="lines")
    ingredient = models.ForeignKey("pos_server.Ingredient", on_delete=models.PROTECT, related_name="restock_lines")
    cases = models.PositiveIntegerField(default=1)
    case_price = models.DecimalField(max_digits=10, decimal_places=2)
    was_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pack_size = models.FloatField(help_text="Units in a case when it was ordered, in the ingredient's unit")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.cases} × {self.ingredient} on {self.restock}"

    @property
    def total(self) -> Decimal:
        return self.case_price * self.cases
