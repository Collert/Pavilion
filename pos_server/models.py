from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.timezone import localtime, now
from gift_cards.models import GiftCard
import json
import uuid
from payments.models import PaymentAuthorization
from django.core.cache import cache
from django.conf import settings

# Create your models here.
    
class Menu(models.Model):
    """
    Model representing a Menu.
    Attributes:
        title (CharField): The title of the menu, with a maximum length of 140 characters.
        is_active (BooleanField): A boolean indicating whether the menu is active. Defaults to False.
        header_image (ImageField): An optional image field for the header image, uploaded to 'files/menu_decorations'.
        footer_image (ImageField): An optional image field for the footer image, uploaded to 'files/menu_decorations'.
        background_color (CharField): The background color of the menu, with a default value of "#ffffff".
        accent_1 (CharField): The first accent color of the menu, with a default value of "#ffffff".
        accent_2 (CharField): The second accent color of the menu, with a default value of "#ffffff".
        accent_3 (CharField): The third accent color of the menu, with a default value of "#ffffff".
    Methods:
        __str__(): Returns a string representation of the Menu instance.
    """
    title = models.CharField(max_length=140)
    is_active = models.BooleanField(default=False)
    header_image = models.ImageField(upload_to='files/menu_decorations', null=True)
    footer_image = models.ImageField(upload_to='files/menu_decorations', null=True)
    background_color = models.CharField(max_length=7, default="#ffffff")
    accent_1 = models.CharField(max_length=7, default="#ffffff")
    accent_2 = models.CharField(max_length=7, default="#ffffff")
    accent_3 = models.CharField(max_length=7, default="#ffffff")

    def __str__(self) -> str:
        return f"Menu: {self.title}"

    def save(self, *args, **kwargs):
        # Exactly one menu is meant to be active: activating this one deactivates the rest
        super().save(*args, **kwargs)
        if self.is_active:
            Menu.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)

    @classmethod
    def active(cls):
        """The active menu, or None if no menu is active."""
        return cls.objects.filter(is_active=True).first()

# How a dish or component is sold (the Availability screen): "off" stops it now, "auto" follows inventory,
# "always" keeps selling past zero (inventory goes negative).
AVAILABILITY = (
    ("off", "Off: stop selling now"),
    ("auto", "Auto: follow inventory"),
    ("always", "Always: sell past zero"),
)


class Dish(models.Model):
    """
    Dish model representing a dish in the POS system.
    Attributes:
        title (CharField): The title of the dish.
        price (DecimalField): The price of the dish.
        image (ImageField): An optional image of the dish.
        description (TextField): An optional description of the dish.
        components (ManyToManyField): Components that make up the dish, related through DishComponent.
        menu (ManyToManyField): Menus that include the dish.
        station (ForeignKey): The Station that prepares the dish.
        in_stock (BooleanField): Every component can supply one right now (kept up to date by update_stock()).
        availability (CharField): Off / Auto / Always (see AVAILABILITY and `available`).
        blocked (BooleanField): One of its components is turned off.
        recipe (ForeignKey): The recipe associated with the dish from the inventory.
        visible_in_menu (BooleanField): Indicates if the dish is visible in the menu.
    Methods:
        __str__(): Returns the title of the dish.
        check_if_only_choice_dish(): Checks whether the dish only consists of components that point to other dishes.
        serialize_with_options(): Serializes the dish with its options and components.
    """
    title = models.CharField(max_length=140)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    image = models.ImageField(upload_to='files/dish_images', null=True, blank=True)
    description = models.TextField(blank=True)
    components = models.ManyToManyField("Component", through='DishComponent')
    menu = models.ManyToManyField("Menu", related_name = "dishes")
    station = models.ForeignKey("Station", related_name="dishes", on_delete=models.PROTECT)
    in_stock = models.BooleanField(default=True)
    availability = models.CharField(max_length=6, choices=AVAILABILITY, default="auto")
    blocked = models.BooleanField(default=False, help_text="One of its components is turned off")
    recipe = models.ForeignKey("inventory.Recipe", related_name="dish", on_delete=models.DO_NOTHING, null=True, blank=True)
    visible_in_menu = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "dishes"

    def __str__(self) -> str:
        return self.title

    @property
    def available(self) -> bool:
        """Sellable now: not turned off, no component turned off, and in stock (or set to sell past zero)."""
        return self.availability != "off" and not self.blocked and (self.availability == "always" or self.in_stock)

    def update_stock(self):
        """Recompute `in_stock` (every component can supply one) and `blocked` (a component is turned off)."""
        parts = list(self.dishcomponent_set.select_related("component"))
        in_stock = all(dc.covers_one() for dc in parts)
        blocked = any(dc.component.availability == "off" for dc in parts)
        if (in_stock, blocked) != (self.in_stock, self.blocked):
            self.in_stock, self.blocked = in_stock, blocked
            self.save(update_fields=["in_stock", "blocked"])


class Combo(models.Model):
    """
    A set of dishes sold together at one price, e.g. "Lunch combo" = burger + side + drink.

    Each ComboSlot ("Main", "Side", "Drink") offers dishes to pick from, each with a price change; one of them
    is the standard pick. Every picked dish is made at its own station, so a combo has no station itself. It
    is listed in the menu section of its first slot's standard dish.
    """
    title = models.CharField(max_length=140)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    image = models.ImageField(upload_to='files/dish_images', null=True, blank=True)
    description = models.TextField(blank=True)
    menu = models.ManyToManyField("Menu", related_name="combos", blank=True)
    visible_in_menu = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.title

    def section_station(self):
        """The station whose menu section lists this combo."""
        slot = self.slots.first()
        option = slot and (slot.default_option() or slot.options.first())
        return option.dish.station if option else None

    @property
    def available(self) -> bool:
        """Sellable while every slot has something to pick (or can be left out)."""
        for slot in self.slots.all():
            if not slot.allow_none and not any(option.dish.available for option in slot.options.all()):
                return False
        return True


class ComboSlot(models.Model):
    """One part of a combo, e.g. "Drink". `ask_on_add` makes the register ask for it when the combo is added."""
    combo = models.ForeignKey(Combo, on_delete=models.CASCADE, related_name="slots")
    title = models.CharField(max_length=60, help_text="e.g. 'Main', 'Side', 'Drink'")
    ask_on_add = models.BooleanField(default=False, help_text="Ask for this pick when the combo is added")
    allow_none = models.BooleanField(default=False, help_text="The customer may leave this part out")
    none_price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"),
                                     help_text="Price change when left out, e.g. -1.50")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.combo}: {self.title}"

    def default_option(self):
        options = list(self.options.all())
        return next((o for o in options if o.is_default), options[0] if options else None)


class ComboSlotOption(models.Model):
    """A dish that can fill a combo slot, and what picking it adds to (or takes off) the combo price."""
    slot = models.ForeignKey(ComboSlot, on_delete=models.CASCADE, related_name="options")
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="combo_options")
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Price change, e.g. 0.50")
    is_default = models.BooleanField(default=False, help_text="The standard pick")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.dish} in {self.slot}"


class OptionGroup(models.Model):
    """
    A pick-one adjustment, e.g. "Patty temperature", "Milk" or "Size".

    It belongs either to a Component (then every dish using that component offers it, and an option can swap
    the component for another, e.g. oat milk) or to a Dish (e.g. "Size", or a salad's "Dressing").
    A dish's customization sheet gathers the groups of the dish and of all its components.
    """
    title = models.CharField(max_length=80)
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="option_groups", null=True, blank=True)
    component = models.ForeignKey("Component", on_delete=models.CASCADE, related_name="option_groups", null=True, blank=True)
    note = models.CharField(max_length=80, blank=True, help_text="Shown next to the title, e.g. 'Cooked to order'")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.CheckConstraint(
            condition=(models.Q(dish__isnull=True) & models.Q(component__isnull=False)) |
                      (models.Q(dish__isnull=False) & models.Q(component__isnull=True)),
            name="option_group_has_one_owner")]

    def __str__(self):
        return f"{self.title} ({self.dish or self.component.title})"

    def default_option(self):
        options = list(self.options.all())
        return next((o for o in options if o.is_default), options[0] if options else None)


class Option(models.Model):
    """
    One choice in an OptionGroup.

    `component`: for a component's group, the component used *instead* (oat milk instead of whole milk); for a
    dish's group, a component used *in addition* (`quantity` of it, e.g. a dressing).
    `multiplier` scales how much is used: the group's component, or every component of the dish for a dish's
    group (e.g. Large = 1.5).
    """
    group = models.ForeignKey(OptionGroup, on_delete=models.CASCADE, related_name="options")
    title = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Price change, e.g. 0.60")
    component = models.ForeignKey("Component", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quantity = models.FloatField(default=1, help_text="Amount of `component` used (dish groups only)")
    multiplier = models.FloatField(default=1)
    is_default = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.group.title}: {self.title}"

    @property
    def available(self) -> bool:
        return self.component is None or self.component.in_stock


class Extra(models.Model):
    """
    A component offered with a dish or combo: an add-on (charged, e.g. avocado or a syrup) or a condiment
    (usually free, counted out "on the side", e.g. ketchup packets or napkins).
    """
    ADDON = "addon"
    CONDIMENT = "condiment"
    kinds = ((ADDON, "Add-on"), (CONDIMENT, "Condiment"))
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="extras", null=True, blank=True)
    combo = models.ForeignKey(Combo, on_delete=models.CASCADE, related_name="extras", null=True, blank=True)
    component = models.ForeignKey("Component", on_delete=models.CASCADE, related_name="extra_uses")
    kind = models.CharField(max_length=10, choices=kinds)
    title = models.CharField(max_length=80, blank=True, help_text="Defaults to the component's title")
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Charged for each one")
    quantity = models.FloatField(default=1, help_text="Amount of the component used for each one")
    max_count = models.PositiveSmallIntegerField(default=4, help_text="Condiments: how many can be asked for")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.CheckConstraint(
            condition=(models.Q(dish__isnull=True) & models.Q(combo__isnull=False)) |
                      (models.Q(dish__isnull=False) & models.Q(combo__isnull=True)),
            name="extra_has_one_owner")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.label} for {self.dish or self.combo}"

    @property
    def label(self):
        return self.title or self.component.title

    @property
    def available(self) -> bool:
        return self.component.in_stock

class Order(models.Model):
    """
    An order from the POS (channel "store"), the online store ("web") or for delivery ("delivery").

    Each station that has to make something for the order gets an OrderStation row holding its status
    (see OrderStation.STATUS_*). Stations without a row are not involved ("not required").

    `picked_up` defaults to True and is set False when any station is needed; the order is done once it
    is True. The money fields are computed on the server when the order is created (see pos_server.orders):
    total = subtotal - discount + tax.
    """
    order_channels = (
        ("store", "In-person"),
        ("web", "Online pickup"),
        ("delivery", "Delivery")
    )
    timestamp = models.DateTimeField(default=timezone.now)
    start_time = models.DateTimeField(default=timezone.now)
    prep_time = models.DurationField(null=True, blank=True)
    stations = models.ManyToManyField("Station", through="OrderStation", related_name="orders")
    name = models.CharField(null=True, max_length = 140, blank=True)
    picked_up = models.BooleanField(default=True)
    special_instructions = models.TextField(null=True, blank=True)
    note_for_kitchen = models.BooleanField(default=True, help_text="Show special_instructions on kitchen screens")
    to_go_order = models.BooleanField(default=False)
    subtotal = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Before discounts and tax")
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    tax = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="What the customer pays")
    channel = models.CharField(max_length=10, choices=order_channels)
    phone = models.PositiveBigIntegerField(null=True, blank=True)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    authorization = models.ForeignKey(PaymentAuthorization, on_delete=models.SET_NULL, null=True, blank=True)
    gift_cards = models.ManyToManyField(GiftCard, through="gift_cards.GiftCardAuthorization", blank=True)

    def save(self, *args, **kwargs):
        if self.pk and not self.prep_time and self.station_statuses.exists() and self.all_stations_done():
            self.prep_time = timezone.now() - self.timestamp
        super().save(*args, **kwargs)
        update_active_orders_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        update_active_orders_cache()
        return result

    def __str__(self) -> str:
        return f"Order {self.id}"

    def station_status_map(self) -> dict:
        """{station code: status} for every station involved. Uses prefetched rows when available."""
        return {row.station.code: row.status for row in self.station_statuses.all()}

    def in_progress_since(self):
        """
        When the order went in progress: the last station's confirmation, or the scheduled start if that's later.
        None while a station still has to confirm. Uses prefetched rows when available.
        """
        rows = list(self.station_statuses.all())
        if not rows or any(row.status == OrderStation.STATUS_PENDING for row in rows):
            return None
        return max([self.start_time] + [row.confirmed_at for row in rows if row.confirmed_at])

    def statuses(self):
        return set(self.station_status_map().values())

    def all_stations_done(self) -> bool:
        return all(status in (OrderStation.STATUS_DONE, OrderStation.STATUS_REJECTED) for status in self.statuses())

    def set_station_status(self, station_codes, status, only_from=None) -> int:
        """
        Move the given stations to `status`. `only_from` limits which current statuses may change
        (e.g. only approve stations that are pending). Returns the number of stations changed.
        """
        from .signals import order_ready
        self.refresh_station_statuses()
        was_done = self.all_stations_done()
        rows = self.station_statuses.filter(station__code__in=station_codes)
        if only_from is not None:
            rows = rows.filter(status__in=only_from)
        stamp = {OrderStation.STATUS_APPROVED: "confirmed_at", OrderStation.STATUS_DONE: "ready_at"}.get(status)
        changed = rows.update(status=status, **({stamp: timezone.now()} if stamp else {}))
        if changed:
            self.refresh_station_statuses()
            self.save()
            if not was_done and self.all_stations_done():
                order_ready.send(sender=Order, order=self)
        return changed

    def refresh_station_statuses(self):
        """Drop prefetched OrderStation rows so the next read hits the database."""
        getattr(self, "_prefetched_objects_cache", {}).pop("station_statuses", None)

    def progress_status(self):
        """
        Customer-facing progress for online/delivery orders:
        1 scheduled, 2 not started, 3 in progress, 4 ready, 5 picked up, 6 delivered. None for POS orders.
        """
        if self.channel not in ("web", "delivery"):
            return None
        statuses = self.statuses()
        if self.channel == "delivery":
            delivery = self.delivery.first()
            if delivery and delivery.completed:
                return 6
        if self.picked_up:
            return 5
        if self.all_stations_done():
            return 4
        if OrderStation.STATUS_APPROVED in statuses:
            return 3
        if self.start_time <= timezone.now():
            return 2
        return 1

class OrderItem(models.Model):
    """
    One line of an order: a dish or a combo, with its customizations.

    A combo line has one child row per slot (the picked dish, `parent` set). Rows with a dish are what the
    kitchen makes: they carry the station and `mods`, the customizations as readable lines ("No onion",
    "Medium rare"). Prices live on top-level rows: `unit_price` includes customizations, `discount` is the
    line discount for the whole line. Titles are copied so old orders still read right if the menu changes.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    dish = models.ForeignKey(Dish, on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items")
    combo = models.ForeignKey(Combo, on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items")
    station = models.ForeignKey("Station", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    title = models.CharField(max_length=140)
    slot = models.CharField(max_length=60, blank=True, help_text="Combo slot this dish fills, e.g. 'Drink'")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    mods = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=140, blank=True)
    selection = models.JSONField(default=dict, blank=True, help_text="The validated choices, for records")

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.title} in order {self.order_id}"

    @property
    def gross(self) -> Decimal:
        return self.unit_price * self.quantity

    @property
    def line_total(self) -> Decimal:
        return self.gross - self.discount


class OrderDiscount(models.Model):
    """A discount applied to an order: to one line (`item`) or to the whole order."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="discounts")
    item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, null=True, blank=True, related_name="discounts")
    discount = models.ForeignKey("Discount", on_delete=models.SET_NULL, null=True, blank=True, related_name="uses")
    label = models.CharField(max_length=80)
    kind = models.CharField(max_length=3, choices=(("pct", "Percent"), ("amt", "Amount")))
    value = models.DecimalField(max_digits=8, decimal_places=2)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    def __str__(self):
        return f"{self.label} -{self.amount} on order {self.order_id}"


class OrderPayment(models.Model):
    """How (part of) an order was paid. Cash payments are tied to the drawer that took them."""
    CASH, CARD, GIFT = "cash", "card", "gift"
    methods = ((CASH, "Cash"), (CARD, "Card"), (GIFT, "Gift card"))
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=5, choices=methods)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    tendered = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Cash handed over")
    drawer = models.ForeignKey("CashDrawerSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    gift_card = models.ForeignKey(GiftCard, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    timestamp = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.get_method_display()} {self.amount} for order {self.order_id}"

    @property
    def change(self) -> Decimal:
        return (self.tendered - self.amount) if self.tendered is not None else Decimal("0.00")


class Discount(models.Model):
    """
    A percent or amount off one item or the whole order.

    Without a `code` it is an *offer*, listed on the register. With a code it is a *coupon*: staff or online
    customers type the code. The conditions below must all hold; blank means "no condition".
    Manual (typed-in) discounts on the register don't use this model and need the
    `pos_server.apply_manual_discount` permission.
    """
    PERCENT, AMOUNT = "pct", "amt"
    kinds = ((PERCENT, "Percent off"), (AMOUNT, "Amount off"))
    ITEM, ORDER, ANY = "item", "order", "any"
    scopes = ((ITEM, "One item"), (ORDER, "Whole order"), (ANY, "Item or order"))

    name = models.CharField(max_length=80)
    note = models.CharField(max_length=120, blank=True, help_text="Shown under the name, e.g. 'Weekdays, 2 to 5 pm'")
    kind = models.CharField(max_length=3, choices=kinds, default=PERCENT)
    value = models.DecimalField(max_digits=8, decimal_places=2, help_text="Percent (10 = 10%) or dollar amount")
    scope = models.CharField(max_length=5, choices=scopes, default=ANY)
    code = models.CharField(max_length=40, unique=True, null=True, blank=True,
                            help_text="Coupon code. Leave blank for an offer listed on the register.")
    active = models.BooleanField(default=True)
    on_register = models.BooleanField(default=True, help_text="Usable on the register")
    online = models.BooleanField(default=False, help_text="Coupon usable in the online store")
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    weekdays = models.CharField(max_length=7, blank=True,
                                help_text="Days it applies: 1 = Monday ... 7 = Sunday, e.g. 12345 for weekdays. Blank for every day.")
    start_time = models.TimeField(null=True, blank=True, help_text="Daily window, e.g. 14:00")
    end_time = models.TimeField(null=True, blank=True)
    min_subtotal = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    min_items = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Items in the order")
    dishes = models.ManyToManyField(Dish, blank=True, related_name="discounts", help_text="Item discounts: only these")
    combos = models.ManyToManyField(Combo, blank=True, related_name="discounts", help_text="Item discounts: only these")
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    times_used = models.PositiveIntegerField(default=0)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        permissions = [("apply_manual_discount", "Can type in a manual discount on the register")]

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper() if self.code else None
        super().save(*args, **kwargs)

    @property
    def is_coupon(self) -> bool:
        return bool(self.code)


class CashDrawerSession(models.Model):
    """
    One register's cash drawer from opening to closing.

    Cash payments taken on the device while it is open link here, so closing compares what was counted
    with what should be there: opening + cash sales + paid in - paid out.
    """
    device = models.ForeignKey("EligibleDevice", on_delete=models.SET_NULL, null=True, blank=True, related_name="drawers")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    opened_at = models.DateTimeField(default=timezone.now)
    opening_amount = models.DecimalField(max_digits=8, decimal_places=2)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    counted_amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    expected_amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"Drawer {self.device or '?'} opened {localtime(self.opened_at):%Y-%m-%d %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def difference(self):
        if self.counted_amount is None or self.expected_amount is None:
            return None
        return self.counted_amount - self.expected_amount


class RegisterSignIn(models.Model):
    """
    Who used a register lately. Updated whenever someone opens the register on a device; its lock screen offers
    PIN sign-in to the most recent few (see pos_server.register.recent_staff). Everyone else signs in with a
    password, which puts them on the list.
    """
    device = models.ForeignKey("EligibleDevice", on_delete=models.CASCADE, null=True, blank=True, related_name="sign_ins")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="register_sign_ins")
    last_used = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_used"]

    def __str__(self):
        return f"{self.user} on {self.device or 'the register'}"

    @classmethod
    def record(cls, device, user):
        cls.objects.update_or_create(device=device, user=user, defaults={"last_used": timezone.now()})


class CashMovement(models.Model):
    """Cash put into (positive) or taken out of (negative) an open drawer outside a sale."""
    drawer = models.ForeignKey(CashDrawerSession, on_delete=models.CASCADE, related_name="movements")
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    reason = models.CharField(max_length=140, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    timestamp = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.amount} ({self.reason})"


class Component(models.Model):
    """
    Component model representing an item in the inventory.
    For more info, refer to: https://github.com/Collert/Pavilion/wiki/Inventory-Management-System
    Attributes:
        units (tuple): Choices for unit of measurement.
        food_types (tuple): Choices for type of food.
        crafting_options (tuple): Choices for crafting options.
        title (CharField): The title of the component.
        ingredients (ManyToManyField): Ingredients associated with the component.
        inventory (FloatField): The current inventory level of the component.
        unit_of_measurement (CharField): The unit of measurement for the component.
        type (CharField): The type of food (food or beverage).
        in_stock (BooleanField): Usable right now (computed on save from `availability` and the inventory).
        availability (CharField): Off / Auto / Always (see AVAILABILITY).
        crafting_option (CharField): The crafting option for the component.
        recipe (ForeignKey): The recipe associated with the component.
        extra_price (DecimalField): Charge for each portion above the standard amount in a dish (an extra patty).
    Methods:
        save(*args, **kwargs): Custom save method to update stock status and related dishes.
        __str__() -> str: String representation of the component.
    """
    units = (
        ("kg", "Kilogram"),
        ("g", "Gram"),
        ("l", "Liter"),
        ("ml", "Milliliter"),
        ("ea", "Each")
    )
    food_types = (
        ("food", "Food"),
        ("beverage", "Beverage")
    )
    crafting_options = (
        ("craft","Craftable"),
        ("buy","Purchased"),
        ("auto","Self-crafting")
    )
    title = models.CharField(max_length=140)
    ingredients = models.ManyToManyField("Ingredient", through='ComponentIngredient')
    inventory = models.FloatField(default = 0, null = True)
    unit_of_measurement = models.CharField(max_length=10, choices=units)
    type = models.CharField(max_length=10, choices=food_types)
    in_stock = models.BooleanField(default=False)
    availability = models.CharField(max_length=6, choices=AVAILABILITY, default="auto")
    crafting_option = models.CharField(max_length=10, choices=crafting_options, default="craft")
    recipe = models.ForeignKey("inventory.Recipe", related_name="component", on_delete=models.DO_NOTHING, null=True, blank=True)
    extra_price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"),
                                      help_text="Charge for each portion above the standard amount, e.g. an extra patty")

    def save(self, *args, **kwargs):
        """
        Recalculate `in_stock` from `availability`: off never, always always, auto while there's inventory
        (self-crafting components are made on demand, so they always count as available). Then bring the dishes
        that use this component up to date.
        """
        if self.availability == "auto":
            self.in_stock = self.crafting_option == "auto" or (self.inventory or 0) > 0
        else:
            self.in_stock = self.availability == "always"
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = {*kwargs["update_fields"], "in_stock"}
        super().save(*args, **kwargs)
        for dc in self.dishcomponent_set.select_related("dish"):
            dc.dish.update_stock()

    def __str__(self) -> str:
        return f"{self.title} ({self.unit_of_measurement.title()})"
    
class DishComponent(models.Model):
    """
    Represents the relationship between a Dish and its Component.
    Attributes:
        dish (ForeignKey): A reference to the Dish model.
        component (ForeignKey): A reference to the Component model.
        quantity (FloatField): The quantity of the component used in one standard portion of the dish.
        adjustable (BooleanField): Customers can leave it out or ask for more (up to max_portions);
            each portion above one costs the component's extra_price.
    Methods:
        __str__(): Returns a string representation of the DishComponent instance.
    """
    dish = models.ForeignKey('Dish', on_delete=models.CASCADE)
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    quantity = models.FloatField()
    adjustable = models.BooleanField(default=False, help_text="Customers can remove it or ask for more")
    max_portions = models.PositiveSmallIntegerField(default=3)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.quantity} x {self.component.title} in {self.dish.title}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.dish.update_stock()

    def delete(self, *args, **kwargs):
        dish = self.dish
        result = super().delete(*args, **kwargs)
        dish.update_stock()
        return result

    def covers_one(self) -> bool:
        """Can the component supply one of this dish right now? Off never; Always and self-crafting always."""
        component = self.component
        if component.availability == "off":
            return False
        if component.availability == "always" or component.crafting_option == "auto":
            return True
        return (component.inventory or 0) >= self.quantity

class Ingredient(models.Model):
    """
    Represents an ingredient in the inventory.
    For more info, refer to: https://github.com/Collert/Pavilion/wiki/Inventory-Management-System
    Attributes:
        units (tuple): Choices for unit of measurement.
        allergens (tuple): Choices for allergens.
        title (str): The name of the ingredient.
        inventory (int): The quantity of the ingredient in inventory.
        unit_of_measurement (str): The unit of measurement for the ingredient.
        allergen (str): The allergen associated with the ingredient.
        unlimited (bool): Indicates if the ingredient has unlimited supply.
        cost (DecimalField): The cost of the ingredient.
    Methods:
        __str__(): Returns a string representation of the ingredient.
    """
    units = (
        ("kg", "Kilogram"),
        ("g", "Gram"),
        ("l", "Liter"),
        ("ml", "Milliliter"),
        ("ea", "Each")
    )
    allergens = (
        ("Eggs", "Eggs"),
        ("Milk", "Milk"),
        ("Mustard", "Mustard"),
        ("Peanuts", "Peanuts"),
        ("Fish", "Fish"),
        ("Sesame seeds", "Sesame seeds"),
        ("Soy", "Soy"),
        ("Sulphites", "Sulphites"),
        ("Tree Nuts", "Tree Nuts"),
        ("Wheat and triticale", "Wheat and triticale"),
        ("Crustaceans", "Crustaceans")
    )
    title = models.CharField(max_length=140)
    inventory = models.IntegerField(default = 0, null = True)
    unit_of_measurement = models.CharField(max_length=10, choices=units)
    allergen = models.CharField(max_length=20, choices=allergens, blank=True, null=True, default=None)
    unlimited = models.BooleanField(default=False)
    cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    def __str__(self) -> str:
        qty = self.inventory
        if qty is None:
            inventory = "Inventory not tracked"
        else:
            inventory = f"{qty} X in inventory"
        return f"{self.title} ({self.unit_of_measurement.title()})"
    
class ComponentIngredient(models.Model):
    """
    Represents the relationship between a component and an ingredient in the POS system.
    Attributes:
        component (ForeignKey): A reference to the Component model.
        ingredient (ForeignKey): A reference to the Ingredient model.
        quantity (FloatField): The quantity of the ingredient used in the component.
    Methods:
        __str__(): Returns a string representation of the ComponentIngredient instance.
    """
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    ingredient = models.ForeignKey('Ingredient', on_delete=models.CASCADE)
    quantity = models.FloatField()

    def __str__(self):
        return f"{self.quantity} x {self.ingredient.title} in {self.component.title}"
    
class EligibleDevice(models.Model):
    """
    Model representing an eligible device.
    Attributes:
        token (UUIDField): A unique identifier for the device, automatically generated.
        name (CharField): The name of the device, with a maximum length of 140 characters.
    Methods:
        __str__(): Returns the name of the device as its string representation.
    """
    token = models.UUIDField(default=uuid.uuid4)
    name = models.CharField(max_length=140)

    def __str__(self) -> str:
        return self.name
    
class Station(models.Model):
    """
    A place where dishes are prepared (kitchen, bar, ...). Each KDS screen picks the stations it covers
    with ?filter=<code>. Menus are grouped into sections by station, using `menu_heading`.
    """
    name = models.CharField(max_length=50, help_text="Shown on kitchen screens, e.g. 'Kitchen'")
    code = models.SlugField(max_length=20, unique=True)
    icon = models.CharField(max_length=20, choices=settings.AVAILABLE_ICONS)
    menu_heading = models.CharField(max_length=80, blank=True, help_text="Menu section title, e.g. 'Hot food'. Defaults to the name.")
    position = models.PositiveSmallIntegerField(default=0, help_text="Order of menu sections and screen filters")

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.name

    @property
    def heading(self):
        return self.menu_heading or self.name

    def as_dict(self):
        return {"code": self.code, "name": self.name, "icon": self.icon, "heading": self.heading}

class OrderStation(models.Model):
    """Status of one station's work on one order."""
    STATUS_PENDING = 0    # waiting for approval (online/delivery orders)
    STATUS_APPROVED = 1   # being made
    STATUS_DONE = 2
    STATUS_REJECTED = 3
    statuses = (
        (STATUS_PENDING, "Pending approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DONE, "Completed"),
        (STATUS_REJECTED, "Rejected"),
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="station_statuses")
    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="order_statuses")
    status = models.PositiveSmallIntegerField(choices=statuses, default=STATUS_PENDING)
    confirmed_at = models.DateTimeField(null=True, blank=True, help_text="When this station confirmed the order")
    ready_at = models.DateTimeField(null=True, blank=True, help_text="When this station marked its part ready")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "station"], name="unique_order_station")]

    def __str__(self):
        return f"{self.station} for {self.order}: {self.get_status_display()}"

def active_orders_queryset():
    """Orders that should be on screens: started, and either still being made or not picked up yet."""
    return Order.objects.filter(
        models.Q(start_time__lte=now()) &
        (models.Q(station_statuses__status__in=[OrderStation.STATUS_PENDING, OrderStation.STATUS_APPROVED]) |
         models.Q(picked_up=False))
    ).distinct()

def update_active_orders_cache():
    """Cache the IDs of active orders under 'active_orders' (60 s)."""
    cache.set('active_orders', list(active_orders_queryset().values_list("id", flat=True)), timeout=60)
