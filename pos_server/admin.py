from django.contrib import admin

from .models import (CashDrawerSession, CashMovement, Combo, ComboSlot, ComboSlotOption, Component, ComponentIngredient,
                     Discount, Dish, DishComponent, EligibleDevice, Extra, Ingredient, Menu, Option, OptionGroup, Order,
                     OrderDiscount, OrderItem, OrderPayment, Station)

@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = ("title", "unit_of_measurement", "inventory", "unlimited", "allergen")
    search_fields = ("title",)
admin.site.register(Menu)
admin.site.register(Station)
admin.site.register(EligibleDevice)


# ---------- menu ----------

class ComponentIngredientInline(admin.TabularInline):
    model = ComponentIngredient
    extra = 1


class ComponentOptionGroupInline(admin.TabularInline):
    """Pick-one adjustments every dish using this component offers (e.g. patty temperature, milk)."""
    model = OptionGroup
    fk_name = "component"
    fields = ("title", "note", "position")
    extra = 0
    show_change_link = True


@admin.register(Component)
class ComponentAdmin(admin.ModelAdmin):
    list_display = ("title", "unit_of_measurement", "inventory", "par", "in_stock", "availability", "crafting_option", "extra_price")
    list_filter = ("type", "crafting_option", "in_stock")
    search_fields = ("title",)
    inlines = [ComponentIngredientInline, ComponentOptionGroupInline]


class DishComponentInline(admin.TabularInline):
    model = DishComponent
    fields = ("component", "quantity", "adjustable", "max_portions", "position")
    autocomplete_fields = ("component",)
    extra = 1


class DishOptionGroupInline(admin.TabularInline):
    """Pick-one adjustments of the dish itself (e.g. size, dressing). Edit the choices on the group's page."""
    model = OptionGroup
    fk_name = "dish"
    fields = ("title", "note", "position")
    extra = 0
    show_change_link = True


class DishExtraInline(admin.TabularInline):
    model = Extra
    fk_name = "dish"
    fields = ("kind", "component", "title", "price", "quantity", "max_count", "position")
    autocomplete_fields = ("component",)
    extra = 0


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("title", "price", "station", "in_stock", "availability", "visible_in_menu")
    list_filter = ("station", "menu", "in_stock")
    search_fields = ("title",)
    inlines = [DishComponentInline, DishOptionGroupInline, DishExtraInline]


class OptionInline(admin.TabularInline):
    model = Option
    fields = ("title", "price", "component", "quantity", "multiplier", "is_default", "position")
    autocomplete_fields = ("component",)
    extra = 1


@admin.register(OptionGroup)
class OptionGroupAdmin(admin.ModelAdmin):
    list_display = ("title", "dish", "component")
    search_fields = ("title", "dish__title", "component__title")
    autocomplete_fields = ("dish", "component")
    inlines = [OptionInline]


class ComboSlotInline(admin.TabularInline):
    """The parts of the combo. Save, then open a slot to list the dishes it offers."""
    model = ComboSlot
    fields = ("title", "ask_on_add", "allow_none", "none_price", "position")
    extra = 0
    show_change_link = True


class ComboExtraInline(admin.TabularInline):
    model = Extra
    fk_name = "combo"
    fields = ("kind", "component", "title", "price", "quantity", "max_count", "position")
    autocomplete_fields = ("component",)
    extra = 0


@admin.register(Combo)
class ComboAdmin(admin.ModelAdmin):
    list_display = ("title", "price", "visible_in_menu")
    list_filter = ("menu",)
    search_fields = ("title",)
    filter_horizontal = ("menu",)
    inlines = [ComboSlotInline, ComboExtraInline]


class ComboSlotOptionInline(admin.TabularInline):
    model = ComboSlotOption
    fields = ("dish", "price", "is_default", "position")
    autocomplete_fields = ("dish",)
    extra = 1


@admin.register(ComboSlot)
class ComboSlotAdmin(admin.ModelAdmin):
    list_display = ("__str__", "ask_on_add", "allow_none")
    inlines = [ComboSlotOptionInline]


# ---------- discounts ----------

@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "kind", "value", "scope", "active", "on_register", "online", "times_used", "max_uses")
    list_filter = ("active", "kind", "scope", "on_register", "online")
    search_fields = ("name", "code")
    filter_horizontal = ("dishes", "combos")
    readonly_fields = ("times_used",)
    fieldsets = (
        (None, {"fields": ("name", "note", "kind", "value", "scope", "code", "active", "position")}),
        ("Where", {"fields": ("on_register", "online")}),
        ("Conditions (leave blank for none)", {"fields": (
            ("starts_on", "ends_on"), "weekdays", ("start_time", "end_time"), "min_subtotal", "min_items",
            "dishes", "combos", ("max_uses", "times_used"))}),
    )


# ---------- orders and the register ----------

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    fields = ("title", "slot", "quantity", "unit_price", "discount", "mods", "note", "station")
    readonly_fields = fields
    extra = 0
    can_delete = False


class OrderDiscountInline(admin.TabularInline):
    model = OrderDiscount
    fields = ("label", "kind", "value", "amount", "item", "applied_by")
    readonly_fields = fields
    extra = 0
    can_delete = False


class OrderPaymentInline(admin.TabularInline):
    model = OrderPayment
    fields = ("method", "amount", "tendered", "gift_card", "drawer", "timestamp")
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "timestamp", "channel", "name", "total", "picked_up")
    list_filter = ("channel", "picked_up")
    search_fields = ("id", "name")
    inlines = [OrderItemInline, OrderDiscountInline, OrderPaymentInline]


class CashMovementInline(admin.TabularInline):
    model = CashMovement
    fields = ("amount", "reason", "user", "timestamp")
    extra = 0


@admin.register(CashDrawerSession)
class CashDrawerSessionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "opened_by", "opening_amount", "closed_at", "counted_amount", "expected_amount")
    list_filter = ("device",)
    inlines = [CashMovementInline]
