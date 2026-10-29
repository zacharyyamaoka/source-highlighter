"""Burn marks into a page image with Pillow (PDF pages and images).

The marks are composited, not drawn with blend modes, so a test can predict every pixel:
inside a highlight a pixel is `source * (1 - a) + colour * a`; outside a spotlight it is `source * (1 - a)`.
"""

from __future__ import annotations

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .geometry import Rect, Style, disjoint_cover


def _pixel_box(rect: Rect) -> tuple[int, int, int, int] | None:
    """Rounded pixel bounds, end-exclusive. WHY rounding: the browser draws the same float rectangle with
    antialiased edges; rounding keeps every edge within half a pixel of it."""
    x0, y0, x1, y1 = round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _fill(draw: ImageDraw.ImageDraw, rect: Rect, fill) -> None:
    box = _pixel_box(rect)
    if box:
        x0, y0, x1, y1 = box
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=fill)


def rgba(color: str, opacity: float) -> tuple[int, int, int, int]:
    red, green, blue = ImageColor.getrgb(color)[:3]
    return red, green, blue, round(max(0.0, min(1.0, opacity)) * 255)


def paint_layer(
    image: Image.Image,
    rects: list[tuple[Rect, str | None]],
    style: Style,
    scale: float,
) -> Image.Image:
    """Composite one style's marks onto `image`. `rects` are (rectangle in output pixels, label)."""
    base = image.convert("RGBA")
    width, height = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    paint = rgba(style.color, style.opacity)
    stroke = style.width * scale
    only_rects = [rect for rect, _ in rects]

    if style.name == "highlight":
        for piece in disjoint_cover(only_rects, width, height, complement=False):
            _fill(draw, piece, paint)
    elif style.name == "box":
        for rect in only_rects:
            outer = Rect(rect.x0 - stroke, rect.y0 - stroke, rect.x1 + stroke, rect.y1 + stroke)
            for side in (
                Rect(outer.x0, outer.y0, outer.x1, rect.y0),
                Rect(outer.x0, rect.y1, outer.x1, outer.y1),
                Rect(outer.x0, rect.y0, rect.x0, rect.y1),
                Rect(rect.x1, rect.y0, outer.x1, rect.y1),
            ):
                _fill(draw, side, paint)
    elif style.name == "underline":
        for rect in only_rects:
            _fill(draw, Rect(rect.x0, rect.y1, rect.x1, rect.y1 + stroke), paint)
    elif style.name in ("spotlight", "blackout"):
        mask = Image.new("L", base.size, paint[3])
        mask_draw = ImageDraw.Draw(mask)
        for rect in only_rects:
            _fill(mask_draw, rect, 0)
        veil = Image.new("RGBA", base.size, paint[:3] + (255,))
        veil.putalpha(mask)
        overlay = veil
        draw = ImageDraw.Draw(overlay)
    else:  # normalize_style already rejects unknown names
        raise ValueError(style.name)

    if style.label:
        font = ImageFont.load_default(size=max(9, round(11 * scale)))
        for rect, label in rects:
            if not label:
                continue
            left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
            pad = round(3 * scale)
            chip = Rect(rect.x0, rect.y0 - (bottom - top) - 2 * pad - 3, rect.x0 + (right - left) + 2 * pad, rect.y0 - 3)
            _fill(draw, chip, (31, 35, 40, 255))
            draw.text((chip.x0 + pad - left, chip.y0 + pad - top), label, font=font, fill=(255, 255, 255, 255))

    return Image.alpha_composite(base, overlay)
