"""
Pavilion's mark, the cloche (static/pavilion/pavilion.svg, and favicon.svg for small sizes), drawn with Pillow for the
PNGs browsers want: the favicon fallbacks in static/pavilion/ (`python -m pavilion.brand` writes them) and the store
app's icons while the store has no logo (online_store/app.py). Keep the shapes in step with the SVGs.
"""
from PIL import Image, ImageDraw

GREEN, AMBER = "#1f4a3a", "#f4a93b"
# In the SVGs' 64-unit box (favicon.svg's 32 doubled). The glint is an arc of the ellipse centred at (32.79, 39.62)
# with radii 13.5 × 12 from 190° to 242°, which is the SVG's "M19.5 37.5A13.5 12 0 0 1 26.5 29".
FULL = {"handle": (32, 19.5, 3.2), "dome": (32, 42.5, 20, 18.5), "glint": (32.79, 39.62, 13.5, 12, 190.2, 242.2, 2.6),
        "plate": (9, 55, 47.5, 4.5), "radius": 17}
SMALL = {"handle": (32, 19.2, 4), "dome": (32, 43, 21, 20), "glint": None, "plate": (8, 56, 49, 5.6), "radius": 17}
SUPERSAMPLE = 4


def draw(size, *, small=None, rounded=True, scale=1.0) -> Image.Image:
    """The mark as a size×size RGBA image. `small`: the favicon's simpler shapes (default below 48px). `rounded`: the
    tile's rounded corners (a maskable icon is full bleed); `scale` shrinks the cloche inside (a maskable icon's safe zone)."""
    shapes = SMALL if (small if small is not None else size < 48) else FULL
    big = size * SUPERSAMPLE
    unit = big / 64
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    if rounded:
        pen.rounded_rectangle((0, 0, big - 1, big - 1), radius=shapes["radius"] * unit, fill=GREEN)
    else:
        pen.rectangle((0, 0, big, big), fill=GREEN)

    def at(x, y):  # the 64-unit box, shrunk by `scale` around its middle
        return ((32 + (x - 32) * scale) * unit, (32 + (y - 32) * scale) * unit)

    def length(v):
        return v * scale * unit

    cx, cy, r = shapes["handle"]
    x, y = at(cx, cy)
    pen.ellipse((x - length(r), y - length(r), x + length(r), y + length(r)), fill=AMBER)
    cx, cy, rx, ry = shapes["dome"]
    x, y = at(cx, cy)
    pen.chord((x - length(rx), y - length(ry), x + length(rx), y + length(ry)), 180, 360, fill=AMBER)
    if shapes["glint"]:
        cx, cy, rx, ry, start, end, width = shapes["glint"]
        x, y = at(cx, cy)
        w = length(width)
        box = (x - length(rx) - w / 2, y - length(ry) - w / 2, x + length(rx) + w / 2, y + length(ry) + w / 2)
        pen.arc(box, start, end, fill=GREEN, width=round(w))
        import math
        for angle in (start, end):  # round caps
            px = x + length(rx) * math.cos(math.radians(angle))
            py = y + length(ry) * math.sin(math.radians(angle))
            pen.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=GREEN)
    left, right, py, width = shapes["plate"]
    (x1, y1), (x2, _y) = at(left, py), at(right, py)
    w = length(width)
    pen.rounded_rectangle((x1 - w / 2, y1 - w / 2, x2 + w / 2, y1 + w / 2), radius=w / 2, fill=AMBER)
    return image.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    from pathlib import Path
    folder = Path(__file__).resolve().parent.parent / "static" / "pavilion"
    draw(32).save(folder / "favicon-32.png", optimize=True)
    draw(180, rounded=False).convert("RGB").save(folder / "apple-touch-icon.png", optimize=True)  # iOS rounds it itself
    print("Wrote", folder / "favicon-32.png", "and", folder / "apple-touch-icon.png")
