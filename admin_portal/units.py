"""
Quantities are stored in the smallest unit (g, ml or ea). Screens show kg / L once the numbers get big.
"""


def split_qty(value, unit) -> tuple[str, str]:
    """(number, unit) for display, e.g. (2500, "g") -> ("2.5", "kg"), (12, "ea") -> ("12", "ea")."""
    value = float(value or 0)
    big = {"g": "kg", "ml": "L"}.get(unit)
    if big and abs(value) >= 1000:
        value /= 1000
        unit = big
        text = f"{value:,.1f}" if abs(value) >= 10 else f"{value:,.2f}"
        return text.rstrip("0").rstrip("."), unit
    if value == int(value):
        return f"{int(value):,}", unit
    return f"{value:,.1f}", unit


def fmt_qty(value, unit) -> str:
    """'2.5 kg', '400 ml', '12 ea'."""
    number, unit = split_qty(value, unit)
    return f"{number} {unit}"
