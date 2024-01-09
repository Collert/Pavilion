from django import template

register = template.Library()

@register.filter(name='has_kitchen_item')
def has_kitchen_item(dishes):
    return any(item["station"] == "kitchen" for item in dishes)

@register.filter(name='has_bar_item')
def has_bar_item(dishes):
    return any(item["station"] == "bar" for item in dishes)
