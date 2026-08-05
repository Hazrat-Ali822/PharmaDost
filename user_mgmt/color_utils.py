"""Pick a brand colour out of an uploaded logo.

Used by the settings screen so a hospital that uploads its logo can have the whole
app themed to match with one click, instead of hunting for a hex code.
"""
from collections import Counter


def dominant_color(image_source):
    """Return the logo's most representative colour as ``#rrggbb``.

    Picks the most common *vivid* pixel — transparent, near-white and near-black
    pixels are skipped (they are background and ink, not the brand), and saturated
    colours are weighted up so a small bright mark beats a large pale wash.
    Returns ``None`` when nothing usable is found. `image_source` may be a path, a
    Django FieldFile, or an uploaded file object.
    """
    from PIL import Image
    try:
        img = Image.open(image_source).convert("RGBA")
    except Exception:
        return None

    img.thumbnail((80, 80))
    counts = Counter()
    for r, g, b, a in img.getdata():
        if a < 128:                       # transparent background
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        if mx > 238 and mn > 238:         # near-white
            continue
        if mx < 32:                       # near-black (ink / outlines)
            continue
        saturation = mx - mn
        weight = 1 + saturation // 40     # favour vivid brand colours
        key = (r // 24 * 24, g // 24 * 24, b // 24 * 24)   # quantise out noise
        counts[key] += weight

    if not counts:
        return None
    r, g, b = counts.most_common(1)[0][0]
    return f"#{r:02x}{g:02x}{b:02x}"


def darker(hex_color, factor=0.72):
    """A darker shade of a hex colour, for a matching accent."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError, AttributeError):
        return hex_color
    r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"
