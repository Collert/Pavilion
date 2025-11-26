from django import template
from datetime import timedelta

register = template.Library()

@register.filter(name='has_kitchen_item')
def has_kitchen_item(dishes):
    return any(item["station"] == "kitchen" for item in dishes)

@register.filter(name='has_bar_item')
def has_bar_item(dishes):
    return any(item["station"] == "bar" for item in dishes)

@register.filter(name='has_station_item')
def has_station_item(dishes, station_code):
    """
    Checks if the dishes list has any item from a specific station.
    :param dishes: List of dish dictionaries.
    :param station_code: The station code to check for.
    :return: True if any dish is from the specified station.
    """
    return any(item["station"] == station_code for item in dishes)

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

    # Map the legacy status fields in the Order model
    legacy_station_status_fields = {
        "kitchen": order.get("kitchen_status", 4),
        "bar": order.get("bar_status", 4),
        "gng": order.get("gng_status", 4),
    }

    # Check legacy stations not covered by filters
    for station, status in legacy_station_status_fields.items():
        if station not in filters and status == 0:  # Pending approval
            return True
    
    # Check dynamic station statuses
    station_statuses = order.get("station_statuses", {})
    for station, status in station_statuses.items():
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

    # Map the legacy status fields in the Order model
    legacy_station_status_fields = {
        "kitchen": order.get("kitchen_status", 4),
        "bar": order.get("bar_status", 4),
        "gng": order.get("gng_status", 4),
    }

    # Check legacy stations covered by filters
    for station, status in legacy_station_status_fields.items():
        if station in filters and status == 0:  # Pending approval
            return True
    
    # Check dynamic station statuses
    station_statuses = order.get("station_statuses", {})
    for station, status in station_statuses.items():
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
    # Check legacy stations
    legacy_ready = (
        (order.get("kitchen_status", 4) == 2 or order.get("kitchen_status", 4) == 4) 
        and 
        (order.get("bar_status", 4) == 2 or order.get("bar_status", 4) == 4) 
        and 
        (order.get("gng_status", 4) == 2 or order.get("gng_status", 4) == 4)
    )
    
    # Check dynamic station statuses - all must be completed (2) or not present
    station_statuses = order.get("station_statuses", {})
    dynamic_ready = all(status in [2, 4] for status in station_statuses.values())
    
    return legacy_ready and dynamic_ready

@register.filter(name='get_station_status')
def get_station_status(order:dict, station_code:str):
    """
    Gets the status for a specific station from the order.
    :param order: An instance of the Order model parsed via collect_order().
    :param station_code: The station code to check.
    :return: Status code (0=pending, 1=approved, 2=completed, 3=rejected, 4=not required).
    """
    # First check dynamic station statuses
    station_statuses = order.get("station_statuses", {})
    if station_code in station_statuses:
        return station_statuses[station_code]
    
    # Fallback to legacy statuses
    legacy_mapping = {
        "kitchen": "kitchen_status",
        "bar": "bar_status",
        "gng": "gng_status",
    }
    if station_code in legacy_mapping:
        return order.get(legacy_mapping[station_code], 4)
    
    return 4  # Not required

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