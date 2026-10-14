from django.contrib import admin

from .models import CookingSession, Recipe, RecipeIngredient, RecipeStep, StockUpdate, Transformation, YieldEntry

admin.site.register(StockUpdate)


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1
    autocomplete_fields = ["ingredient"]


class RecipeStepInline(admin.StackedInline):
    model = RecipeStep
    extra = 1
    fields = (("position", "title"), "text", ("duration", "scene"), "uses")
    filter_horizontal = ("uses",)


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    """Recipes for Cookery: save the ingredients first, then the steps can say which ones they use."""
    list_display = ("__str__", "yield_quantity")
    inlines = [RecipeIngredientInline, RecipeStepInline]


@admin.register(Transformation)
class TransformationAdmin(admin.ModelAdmin):
    list_display = ("name", "from_component", "to_component", "ratio", "duration", "scene")
    autocomplete_fields = ["from_component", "to_component"]


class YieldEntryInline(admin.TabularInline):
    model = YieldEntry
    extra = 0


@admin.register(CookingSession)
class CookingSessionAdmin(admin.ModelAdmin):
    """Batches in Cookery, ongoing and finished (finished ones are the record of what was made)."""
    list_display = ("__str__", "holder", "started_at", "finished_at", "made", "expected", "cancelled")
    list_filter = ("kind", "cancelled")
    inlines = [YieldEntryInline]
