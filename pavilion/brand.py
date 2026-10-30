"""
Pavilion's mark, the cloche (static/pavilion/pavilion.svg, and favicon.svg for small sizes), drawn with Pillow for the
PNGs browsers want: the favicon fallbacks in static/pavilion/ and the store app's icons while the store has no logo
(online_store/app.py). The courier app's mark, the bag (deliveries/static/deliveries/courier.svg), is drawn the same way
for its icons. `python -m pavilion.brand` writes every static PNG. Keep the shapes in step with the SVGs.
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


def draw_bag(size, *, rounded=True, scale=1.0, badge=False) -> Image.Image:
    """The courier app's bag (courier.svg) as a size×size RGBA image. `badge`: white on nothing, for Android's status
    bar (it only reads the shape)."""
    big = size * SUPERSAMPLE
    unit = big / 64
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    ink = "#ffffff" if badge else AMBER
    if not badge:
        if rounded:
            pen.rounded_rectangle((0, 0, big - 1, big - 1), radius=17 * unit, fill=GREEN)
        else:
            pen.rectangle((0, 0, big, big), fill=GREEN)
    if badge:
        scale *= 1.25  # nothing around it: let it fill the badge

    def at(x, y):
        return ((32 + (x - 32) * scale) * unit, (32 + (y - 32) * scale) * unit)

    w = 4.8 * scale * unit

    def line(a, b):
        pen.line([at(*a), at(*b)], fill=ink, width=round(w))
        for point in (a, b):  # round caps
            x, y = at(*point)
            pen.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=ink)

    # handle: two posts and the top of a circle (centre 39.2, 21.6; radius 7.2)
    line((32, 25.2), (32, 21.6))
    line((46.4, 25.2), (46.4, 21.6))
    (cx, cy), r = at(39.2, 21.6), 7.2 * scale * unit
    pen.arc((cx - r - w / 2, cy - r - w / 2, cx + r + w / 2, cy + r + w / 2), 180, 360, fill=ink, width=round(w))
    pen.polygon([at(24.8, 25.2), at(53.6, 25.2), at(51.2, 51.6), at(27.2, 51.6)], fill=ink)
    line((10.4, 32.4), (20, 32.4))
    line((12.8, 42), (22.4, 42))
    return image.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    from pathlib import Path
    folder = Path(__file__).resolve().parent.parent / "static" / "pavilion"
    draw(32).save(folder / "favicon-32.png", optimize=True)
    draw(180, rounded=False).convert("RGB").save(folder / "apple-touch-icon.png", optimize=True)  # iOS rounds it itself
    courier = folder.parent.parent / "deliveries" / "static" / "deliveries"
    for n in (192, 512):
        draw_bag(n).save(courier / f"icon-{n}x{n}.png", optimize=True)
    draw_bag(512, rounded=False, scale=0.8).convert("RGB").save(courier / "icon-maskable.png", optimize=True)
    draw_bag(180, rounded=False).convert("RGB").save(courier / "apple-touch-icon.png", optimize=True)
    draw_bag(96, badge=True).save(courier / "icon-monochrome.png", optimize=True)
    draw_bag(32).save(courier / "favicon-32.png", optimize=True)
    print("Wrote the favicons in", folder, "and the courier app's icons in", courier)
