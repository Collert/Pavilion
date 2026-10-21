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
    A menu: the dishes and combos sold while it's live. Several menus can be live at once (pos_server.menu_schedule):
    a menu switched by hand is live while `manual_on`; an automatic one follows its MenuRules. Drafts are never live.
    `position` is the priority: the register, online store and screens list live menus in this order, and the first
    one is the default (Menu.active()). `display` is its menu-board layout (the admin's Display tab). The colour and
    image fields are from the old menu theming.
    """
    title = models.CharField(max_length=140)
    position = models.PositiveSmallIntegerField(default=0, help_text="Priority: lower comes first")
    draft = models.BooleanField(default=False, help_text="Not published yet, so never live")
    auto = models.BooleanField(default=False, help_text="Turned on and off by its rules, not by hand")
    manual_on = models.BooleanField(default=False, help_text="Switched by hand: on now")
    header_image = models.ImageField(upload_to='files/menu_decorations', null=True, blank=True)
    footer_image = models.ImageField(upload_to='files/menu_decorations', null=True, blank=True)
    background_color = models.CharField(max_length=7, default="#ffffff")
    accent_1 = models.CharField(max_length=7, default="#ffffff")
    accent_2 = models.CharField(max_length=7, default="#ffffff")
    accent_3 = models.CharField(max_length=7, default="#ffffff")
    display = models.JSONField(default=dict, blank=True, help_text="How menu boards show it (see screens/menu_boards.py)")

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"Menu: {self.title}"

    @classmethod
    def live_menus(cls, now=None):
        """Every menu live right now, by priority."""
        from .menu_schedule import live_menus
        return live_menus(now)

    @classmethod
    def active(cls, now=None):
        """The top live menu (the default for the register and online store), or None if nothing is live."""
        live = cls.live_menus(now)
        return live[0] if live else None

    def is_live(self, now=None):
        from .menu_schedule import Schedule
        return Schedule().is_live(self, now)


class MenuRule(models.Model):
    """
    Turns a menu on or off (design: AdminMenus, schedule tab): every week on `days` at `time`, every day between two
    dates, or (off only) when another menu turns on.
    """
    ON, OFF = "on", "off"
    WEEKLY, DATES, MENU = "weekly", "dates", "menu"
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="rules")
    kind = models.CharField(max_length=3, choices=((ON, "Turn on"), (OFF, "Turn off")))
    type = models.CharField(max_length=6, choices=((WEEKLY, "Weekly time"), (DATES, "Date range"), (MENU, "Another menu starts")))
    days = models.CharField(max_length=7, blank=True, help_text="Weekdays, Monday = 0, e.g. '01234'")
    time = models.TimeField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    other_menu = models.ForeignKey(Menu, on_delete=models.CASCADE, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.kind} {self.type} for {self.menu_id}"

    @property
    def day_list(self):
        return sorted({int(d) for d in self.days if d.isdigit() and int(d) < 7})


# Everything is counted in its smallest unit (grams, millilitres or pieces); screens show kilos and litres when
# the numbers get big. Quantities of a component or ingredient anywhere (recipes, dish parts, stock) use its unit.
UNITS = (
    ("g", "Grams"),
    ("ml", "Millilitres"),
    ("ea", "Each"),
)

# How a dish or component is sold (the Availability screen): "off" stops it now, "auto" follows inventory,
# "always" keeps selling past zero (inventory goes negative).
AVAILABILITY = (
    ("off", "Off: stop selling now"),
    ("auto", "Auto: follow inventory"),
    ("always", "Always: sell past zero"),
)


# The allergens an ingredient can carry (and a customer can tell the kitchen about)
ALLERGENS = ["Gluten", "Dairy", "Egg", "Soy", "Peanuts", "Tree nuts", "Sesame", "Mustard", "Fish", "Shellfish", "Sulphites"]

# Diets a dish or ingredient can fit: key, label, short badge, test on the combined flags (Ingredient.flags)
DIETS = [
    ("vegetarian", "Vegetarian", "V", lambda f: not f["meat"] and not f["fish"]),
    ("vegan", "Vegan", "VG", lambda f: not f["meat"] and not f["fish"] and not f["animal"]),
    ("pescatarian", "Pescatarian", "P", lambda f: not f["meat"]),
    ("gluten_free", "Gluten-free", "GF", lambda f: not f["gluten"]),
]


class Dish(models.Model):
    """
    Something sold on its own or in a combo, made at `station` from its components (DishComponent).

    `availability` is Off / Auto / Always (see AVAILABILITY and `available`); `in_stock` and `blocked` are kept up
    to date by update_stock(). `ticket_name` is the short name on kitchen tickets, `prep_minutes` how long it
    usually takes. With `catering` on it can be ordered for catering (at least `catering_min`), priced by the
    best CateringPrice break for the quantity. Archived dishes are off every menu and hidden from the admin list.
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
    ticket_name = models.CharField(max_length=14, blank=True, help_text="Short name for kitchen tickets")
    prep_minutes = models.PositiveSmallIntegerField(default=5)
    archived = models.BooleanField(default=False)
    catering = models.BooleanField(default=False, help_text="Can be ordered for catering")
    catering_min = models.PositiveSmallIntegerField(default=10, help_text="Smallest catering order")

    class Meta:
        verbose_name_plural = "dishes"

    def __str__(self) -> str:
        return self.title

    @property
    def available(self) -> bool:
        """Sellable now: not archived or turned off, no component turned off, and in stock (or set to sell past zero)."""
        return not self.archived and self.availability != "off" and not self.blocked and (self.availability == "always" or self.in_stock)

    def catering_price(self, quantity: int) -> Decimal:
        """Price each for a catering order of `quantity`: the biggest break it reaches, else the retail price."""
        price = self.price
        for tier in self.catering_prices.all():
            if quantity >= tier.quantity:
                price = tier.price
        return price

    def update_stock(self):
        """Recompute `in_stock` (every component can supply one) and `blocked` (a component is turned off)."""
        parts = list(self.dishcomponent_set.select_related("component"))
        in_stock = all(dc.covers_one() for dc in parts)
        blocked = any(dc.component.availability == "off" for dc in parts)
        if (in_stock, blocked) != (self.in_stock, self.blocked):
            self.in_stock, self.blocked = in_stock, blocked
            self.save(update_fields=["in_stock", "blocked"])


class CateringPrice(models.Model):
    """A quantity break for catering: `price` each when `quantity` or more are ordered."""
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="catering_prices")
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ["quantity", "id"]

    def __str__(self):
        return f"{self.dish}: {self.quantity}+ at {self.price}"


class Combo(models.Model):
    """
    A set of dishes sold together, e.g. "Lunch combo" = burger + side + drink.

    Each ComboSlot is one choice ("Main", "Side", "Drink"): required ones are filled with their default dish unless
    the customer picks another, optional ones (a treat) are skipped unless picked. `pricing` FIXED: `price` plus
    each pick's upcharge (ComboSlotOption.price); PERCENT: the picks' own prices less `percent_off`. Customizations of
    the picks are added on top. Every pick is made at its own station; kitchen tickets call the combo
    `ticket_name`. Only `active` combos show on menus; archived ones are hidden from the admin list too.
    """
    FIXED = "fixed"
    PERCENT = "percent"
    PRICINGS = ((FIXED, "Set price"), (PERCENT, "Percent off"))

    title = models.CharField(max_length=140)
    ticket_name = models.CharField(max_length=12, blank=True, help_text="Name on kitchen tickets")
    pricing = models.CharField(max_length=7, choices=PRICINGS, default=FIXED)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Set price")
    percent_off = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("15.00"), help_text="Percent off")
    image = models.ImageField(upload_to='files/dish_images', null=True, blank=True)
    description = models.TextField(blank=True)
    menu = models.ManyToManyField("Menu", related_name="combos", blank=True)
    active = models.BooleanField(default=True, help_text="Hidden combos don't show on any menu or at the register")
    archived = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.title

    def section_station(self):
        """The station whose menu section lists this combo."""
        slot = self.slots.first()
        option = slot and (slot.default_option() or slot.options.first())
        return option.dish.station if option else None

    @property
    def available(self) -> bool:
        """Sellable while it's active and every required choice has a dish that isn't sold out."""
        if not self.active or self.archived:
            return False
        for slot in self.slots.all():
            if slot.required and not any(option.dish.available for option in slot.options.all()):
                return False
        return True

    def unit_price(self, picks) -> Decimal:
        """The combo's price for [(ComboSlotOption)] picked, before the picks' customizations."""
        if self.pricing == self.PERCENT:
            total = sum((option.dish.price for option in picks), Decimal("0"))
            return (total * (100 - self.percent_off) / 100).quantize(Decimal("0.01"))
        return self.price + sum((option.price for option in picks), Decimal("0"))

    def default_picks(self):
        return [option for slot in self.slots.all() for option in slot.default_picks()]

    def standard_price(self) -> Decimal:
        """The price with every default pick."""
        return self.unit_price(self.default_picks())


class ComboSlot(models.Model):
    """
    One choice in a combo, e.g. "Side": `picks` dishes from its options (the same one may be picked twice).
    A required choice starts on its default dish (then the next options, for more than one pick); an optional
    one starts empty and takes up to `picks`.
    """
    combo = models.ForeignKey(Combo, on_delete=models.CASCADE, related_name="slots")
    title = models.CharField(max_length=60, help_text="e.g. 'Main', 'Side', 'Drink'")
    picks = models.PositiveSmallIntegerField(default=1)
    required = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.combo}: {self.title}"

    def default_option(self):
        options = list(self.options.all())
        return next((o for o in options if o.is_default), options[0] if options else None)

    def default_picks(self):
        """
        What a required choice starts with: the default, then the other options in order, skipping sold-out dishes
        when there are enough others (the register does the same). Empty when optional.
        """
        if not self.required:
            return []
        options = list(self.options.all())
        default = self.default_option()
        ordered = ([default] if default else []) + [o for o in options if o != default]
        open_ = [o for o in ordered if o.dish.available]
        return (open_ if len(open_) >= self.picks else ordered)[:self.picks]


class ComboSlotOption(models.Model):
    """A dish that can fill a combo slot, and what picking it adds to (or takes off) the combo price."""
    slot = models.ForeignKey(ComboSlot, on_delete=models.CASCADE, related_name="options")
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="combo_options")
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Upcharge (set-price combos)")
    is_default = models.BooleanField(default=False, help_text="The default pick")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.dish} in {self.slot}"


class OptionGroup(models.Model):
    """
    A modifier group, e.g. "Doneness" (pick one), "Add-ons" (pick any) or "Size".

    `pick` ONE: one option at most; a `required` group starts on its first option (the standard), an optional
    one on nothing. ANY: each option can be added any number of times (up to MAX_PICKS); a `required` one needs
    at least one. It belongs either to a Component (then every dish using that component offers it, and a pick-one
    option can swap the component for another, e.g. oat milk) or to a Dish. A dish's customization sheet gathers
    the groups of the dish and of all its components.
    """
    ONE = "one"
    ANY = "any"
    PICKS = ((ONE, "Pick one"), (ANY, "Pick any"))
    MAX_PICKS = 99

    title = models.CharField(max_length=80)
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="option_groups", null=True, blank=True)
    component = models.ForeignKey("Component", on_delete=models.CASCADE, related_name="option_groups", null=True, blank=True)
    pick = models.CharField(max_length=3, choices=PICKS, default=ONE)
    required = models.BooleanField(default=False)
    addons = models.BooleanField(default=False, help_text="The dish's built-in Add-ons group: pick any, each option a component")
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
        """The standard pick: the first option of a required pick-one group, else none."""
        if self.pick != self.ONE or not self.required:
            return None
        options = list(self.options.all())
        return options[0] if options else None


class Option(models.Model):
    """
    One choice in an OptionGroup; `price` is charged each time it's picked.

    `component`: in a component's pick-one group, the component used *instead* (oat milk instead of whole milk);
    otherwise a component used *in addition*, `quantity` of it each time (a dressing, a slice of bacon).
    `multiplier` (pick-one only) scales how much is used: the group's component, or every component of the dish
    for a dish's group (e.g. Large = 1.5).
    """
    group = models.ForeignKey(OptionGroup, on_delete=models.CASCADE, related_name="options")
    title = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Price change, e.g. 0.60")
    component = models.ForeignKey("Component", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quantity = models.FloatField(default=1, help_text="Amount of `component` used (dish groups only)")
    multiplier = models.FloatField(default=1)
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
    A condiment offered with a dish or combo: usually free, counted out "on the side" (ketchup packets, napkins).
    Charged add-ons are options in a dish's pick-any modifier group.
    """
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="extras", null=True, blank=True)
    combo = models.ForeignKey(Combo, on_delete=models.CASCADE, related_name="extras", null=True, blank=True)
    component = models.ForeignKey("Component", on_delete=models.CASCADE, related_name="extra_uses")
    title = models.CharField(max_length=80, blank=True, help_text="Defaults to the component's title")
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"), help_text="Charged for each one")
    quantity = models.FloatField(default=1, help_text="Amount of the component used for each one")
    max_count = models.PositiveSmallIntegerField(default=4, help_text="How many can be asked for")
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
    device = models.ForeignKey("EligibleDevice", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                               help_text="The register it was rung up on")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="orders", help_text="The signed-in customer who placed it")
    email = models.EmailField(blank=True, help_text="Where receipts go, when the customer gave one")
    staff_note = models.TextField(blank=True, help_text="Only staff see this")
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

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
            if delivery and delivery.delivered_at:
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
    upcharge = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"),
                                   help_text="A combo pick's upcharge (set-price combos), for reports")
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
    # Card payments: what Square reported (filled in after the payment, see payments.square.fill_card_details)
    square_checkout_id = models.CharField(max_length=64, blank=True, help_text="The Terminal checkout that took it")
    square_payment_id = models.CharField(max_length=64, blank=True)
    card_brand = models.CharField(max_length=24, blank=True, help_text="e.g. VISA, MASTERCARD")
    card_last4 = models.CharField(max_length=4, blank=True)
    entry_method = models.CharField(max_length=12, blank=True, help_text="tap, chip, swipe, keyed or online")

    def __str__(self):
        return f"{self.get_method_display()} {self.amount} for order {self.order_id}"

    @property
    def card_label(self) -> str:
        """'Visa •••• 4242', or 'Card' when Square didn't say."""
        if not self.card_last4:
            return "Card"
        return f"{CARD_BRANDS.get(self.card_brand, self.card_brand.title() or 'Card')} •••• {self.card_last4}"

    @property
    def change(self) -> Decimal:
        return (self.tendered - self.amount) if self.tendered is not None else Decimal("0.00")


CARD_BRANDS = {"VISA": "Visa", "MASTERCARD": "Mastercard", "AMERICAN_EXPRESS": "Amex", "DISCOVER": "Discover",
               "INTERAC": "Interac", "JCB": "JCB", "CHINA_UNIONPAY": "UnionPay", "SQUARE_GIFT_CARD": "Square gift card"}


class Refund(models.Model):
    """
    Money given back on an order (pos_server.refunds.refund_order). `parts` says where it went:
    [{"method": "card"|"gift"|"cash", "amount": "4.50", "ref": "<Square refund id or gift card number>"}].
    """
    REASONS = ["Wrong order", "Quality issue", "Long wait", "Changed their mind", "Charged twice", "Item unavailable"]
    ORIGINAL, CASH = "original", "cash"
    targets = ((ORIGINAL, "Original payment"), (CASH, "Cash from a drawer"))
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="refunds")
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    reason = models.CharField(max_length=80)
    to = models.CharField(max_length=10, choices=targets)
    whole = models.BooleanField(default=False, help_text="Everything left on the order")
    parts = models.JSONField(default=list, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Refund {self.amount} on order {self.order_id}"


class RefundItem(models.Model):
    refund = models.ForeignKey(Refund, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="refunded")
    quantity = models.PositiveIntegerField()


class ReceiptLog(models.Model):
    """A receipt given for an order: printed, or sent by email (other channels are placeholders for now)."""
    PRINT, EMAIL, SMS, WHATSAPP, TELEGRAM, MESSENGER = "print", "email", "sms", "whatsapp", "telegram", "messenger"
    channels = ((PRINT, "Printed"), (EMAIL, "Email"), (SMS, "Text"), (WHATSAPP, "WhatsApp"), (TELEGRAM, "Telegram"),
                (MESSENGER, "Messenger"))
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="receipts")
    channel = models.CharField(max_length=10, choices=channels)
    to = models.CharField(max_length=140, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at"]


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
    units = UNITS
    food_types = (
        ("food", "Food"),
        ("beverage", "Beverage")
    )
    crafting_options = (
        ("craft","Craftable"),
        ("buy","Purchased"),
        ("auto","Self-crafting"),
        ("transform", "Made by a transform"),  # e.g. frozen pierogi: only ever made from another component (Cookery)
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
    par = models.FloatField(null=True, blank=True, help_text="How much to keep on hand (Cookery shows how close you are)")
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
        adjustable (BooleanField): Customers can leave it out or ask for more, up to `max_extra` steps above the
            standard. A step is one piece for whole pieces (8 pierogies: 0 to 18), else the whole amount (20 g of
            lettuce: none, standard, double...). Each step above the standard costs the component's extra_price.
    Methods:
        __str__(): Returns a string representation of the DishComponent instance.
    """
    dish = models.ForeignKey('Dish', on_delete=models.CASCADE)
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    quantity = models.FloatField()
    adjustable = models.BooleanField(default=False, help_text="Customers can remove it or ask for more")
    max_extra = models.PositiveSmallIntegerField(default=10, help_text="How many steps above the standard can be asked for")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.quantity} x {self.component.title} in {self.dish.title}"

    @property
    def step(self) -> float:
        """The amount one tap adds or removes: a piece when the dish uses whole pieces, else the whole amount."""
        if self.component.unit_of_measurement == "ea" and self.quantity >= 1 and float(self.quantity).is_integer():
            return 1.0
        return self.quantity

    @property
    def standard(self) -> int:
        """How many steps the dish comes with."""
        return round(self.quantity / self.step) if self.step else 1

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
    Something bought in and used in components (flour, milk, ground beef), counted in `unit_of_measurement`
    (g, ml or ea). `cost` is the price of one unit. `allergens` lists entries of ALLERGENS; `meat`, `fish` and
    `animal` (dairy, eggs, honey, gelatin...) decide which diets it fits (see `diets()`). Dishes inherit both
    from the ingredients of their components.
    """
    units = UNITS
    title = models.CharField(max_length=140)
    inventory = models.FloatField(default=0, null=True)
    unit_of_measurement = models.CharField(max_length=10, choices=units)
    allergens = models.JSONField(default=list, blank=True, help_text="Entries of ALLERGENS")
    meat = models.BooleanField(default=False, help_text="Meat or poultry")
    fish = models.BooleanField(default=False, help_text="Fish or seafood")
    animal = models.BooleanField(default=False, help_text="Animal byproduct: dairy, eggs, honey, gelatin")
    unlimited = models.BooleanField(default=False)
    cost = models.DecimalField(max_digits=14, decimal_places=6, blank=True, null=True, help_text="Cost of one unit (a gram, a millilitre or a piece)")

    @property
    def flags(self) -> dict:
        """What decides the diets, with the flags the allergens imply (dairy or egg is an animal product...)."""
        allergens = set(self.allergens or [])
        return {"meat": self.meat, "fish": self.fish or bool(allergens & {"Fish", "Shellfish"}),
                "animal": self.animal or bool(allergens & {"Dairy", "Egg"}), "gluten": "Gluten" in allergens}

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
    color = models.CharField(max_length=7, default="#1F4A3A", help_text="Used on kitchen screens, tickets and reports")

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
    """
    Orders that should be on screens: started, and either still being made or not picked up yet. Delivery orders
    wait for a courier to accept them first (deliveries.models.hidden_order_ids).
    """
    from deliveries.models import hidden_order_ids
    return Order.objects.filter(
        models.Q(start_time__lte=now()) &
        (models.Q(station_statuses__status__in=[OrderStation.STATUS_PENDING, OrderStation.STATUS_APPROVED]) |
         models.Q(picked_up=False))
    ).exclude(id__in=hidden_order_ids()).distinct()

def update_active_orders_cache():
    """Cache the IDs of active orders under 'active_orders' (60 s)."""
    cache.set('active_orders', list(active_orders_queryset().values_list("id", flat=True)), timeout=60)
