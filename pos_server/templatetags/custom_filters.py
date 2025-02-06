from django import template
from datetime import timedelta

register = template.Library()

@register.filter(name='has_kitchen_item')
def has_kitchen_item(dishes):
    return any(item["station"] == "kitchen" for item in dishes)

@register.filter(name='has_bar_item')
def has_bar_item(dishes):
    return any(item["station"] == "bar" for item in dishes)

@register.filter(name='has_options')
def has_options(dish):
    return len(dish.serialize_with_options()["fields"]["choice_components"]) > 0

@register.filter(name='only_choices')
def only_choices(dish):
    return dish.check_if_only_choice_dish()

@register.filter(name='pending_other_stations')
def pending_other_stations(order:dict, filters:list):
    """
    Checks if the order has any station pending approval that is not in the provided filters.
    :param order: An instance of the Order model parsed via collect_order().
    :param filters: A list of station names covered by the device (e.g., ["kitchen", "bar"]).
    :return: True if there are pending stations outside the filters, False otherwise.
    """
    # Ensure filters is a list
    if not filters:
        filters = []

    # Map the status fields in the Order model
    station_status_fields = {
        "kitchen": order["kitchen_status"],
        "bar": order["bar_status"],
        "gng": order["gng_status"],
    }

    # Check stations not covered by filters
    for station, status in station_status_fields.items():
        if station not in filters and status == 0:  # Pending approval
            return True

    return False

@register.filter(name='pending_self')
def pending_self(order:dict, filters:list):
    """
    Checks if the order has station covered by the filters pending approval.
    :param order: An instance of the Order model parsed via collect_order().
    :param filters: A list of station names covered by the device (e.g., ["kitchen", "bar"]).
    :return: True if there are pending stations in the filters, False otherwise.
    """
    # Ensure filters is a list
    if not filters:
        filters = []

    # Map the status fields in the Order model
    station_status_fields = {
        "kitchen": order["kitchen_status"],
        "bar": order["bar_status"],
        "gng": order["gng_status"],
    }

    # Check stations not covered by filters
    for station, status in station_status_fields.items():
        if station in filters and status == 0:  # Pending approval
            return True

    return False

@register.filter(name='all_stations_ready')
def all_stations_ready(order:dict):
    """
    Checks if the order is ready in all stations.
    :param order: An instance of the Order model parsed via collect_order().
    :return: True if order is ready in all productions stations, False otherwise.
    """
    return (
        (order["kitchen_status"] == 2 or order["kitchen_status"] == 4) 
        and 
        (order["bar_status"] == 2 or order["bar_status"] == 4) 
        and 
        (order["gng_status"] == 2 or order["gng_status"] == 4)
    )

@register.filter(name='format_duration')
def format_duration(value: timedelta):
    try:
        # Extract total seconds from timedelta
        total_seconds = int(value.total_seconds())
        
        # Calculate minutes and seconds
        minutes = total_seconds // 60
        seconds = total_seconds % 60

        # Format the string as MM:SS
        return f"{minutes:02d}:{seconds:02d}"
    except (ValueError, TypeError, AttributeError):
        return ""