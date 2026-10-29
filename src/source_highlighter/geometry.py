"""Styles, Locations and the one geometry rule both forms share.

The interactive form (js/source-highlighter.js) states the same rule at its top; tests measure that the two
draw the same rectangles. Lengths (padding, stroke width) are in *source units*: PDF points, image pixels,
CSS pixels for HTML and text. A render at zoom/scale `s` multiplies every length by `s`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# WHY these exact numbers: js/source-highlighter.js STYLE_DEFAULTS holds the same table, and
# tests/test_parity.py reads both and fails if they drift.
STYLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "highlight": {"color": "#ffe600", "opacity": 0.45, "padding": 2, "width": 0},
    "box": {"color": "#e5322d", "opacity": 1, "padding": 2, "width": 2},
    "underline": {"color": "#e5322d", "opacity": 1, "padding": 2, "width": 2},
    "spotlight": {"color": "#000000", "opacity": 0.6, "padding": 2, "width": 0},
    "blackout": {"color": "#000000", "opacity": 1, "padding": 2, "width": 0},
}
STYLES = tuple(STYLE_DEFAULTS)


@dataclass(frozen=True)
class Style:
    name: str
    color: str
    opacity: float
    padding: float
    width: float
    label: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "color": self.color,
            "opacity": self.opacity,
            "padding": self.padding,
            "width": self.width,
            "label": self.label,
        }


def normalize_style(style: str | dict | Style | None) -> Style:
    """Fill in a style's defaults. Accepts "spotlight", a Style, or {"name": "spotlight", "color": …}."""
    if isinstance(style, Style):
        return style
    spec = {"name": style} if isinstance(style, str) or style is None else dict(style)
    name = spec.get("name") or spec.get("style") or "highlight"
    if name not in STYLE_DEFAULTS:
        raise ValueError(f'unknown style "{name}" (expected one of {", ".join(STYLES)})')
    merged = {**STYLE_DEFAULTS[name], "label": False}
    for key in ("color", "opacity", "padding", "width", "label"):
        if spec.get(key) is not None:
            merged[key] = spec[key]
    return Style(name=name, **merged)


@dataclass
class Layer:
    locations: list[dict]
    style: Style


def normalize_layers(locations: Any, style: str | dict | Style | None = None) -> list[Layer]:
    """One style per call, or a list of {locations, style} layers (CONTRACT §3).

    `locations` may be a list of Locations, {"locations": [...]}, {"layers": [...]} or a list of layers.
    """
    if isinstance(locations, dict) and "layers" in locations:
        locations = locations["layers"]
    elif isinstance(locations, dict) and "locations" in locations:
        style = style if style is not None else locations.get("style")
        locations = locations["locations"]
    if isinstance(locations, list) and locations and all(isinstance(item, dict) and "locations" in item for item in locations):
        return [Layer(list(layer["locations"]), normalize_style(layer.get("style", style))) for layer in locations]
    return [Layer(list(locations or []), normalize_style(style))]


def location_kind(location: Any) -> str | None:
    """'page', 'html' or 'text' — or None when the Location is not exactly one of them."""
    if not isinstance(location, dict):
        return None
    kinds = [key for key in ("box", "html", "text") if key in location]
    if len(kinds) != 1:
        return None
    return "page" if kinds[0] == "box" else kinds[0]


def is_valid_box(box: Any) -> bool:
    return (
        isinstance(box, (list, tuple))
        and len(box) == 4
        and all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1 for value in box)
        and box[0] < box[2]
        and box[1] < box[3]
    )


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def scaled(self, factor: float) -> Rect:
        return Rect(self.x0 * factor, self.y0 * factor, self.x1 * factor, self.y1 * factor)

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


def clip_rect(rect: Rect, width: float, height: float) -> Rect:
    return Rect(max(0.0, rect.x0), max(0.0, rect.y0), min(width, rect.x1), min(height, rect.y1))


def page_box_rect(box: list[float], page_width: float, page_height: float, padding: float) -> Rect:
    """A page/box Location as a rectangle in source units: scaled to the page, padded, clipped to the page."""
    x0, y0, x1, y1 = box
    return clip_rect(
        Rect(x0 * page_width - padding, y0 * page_height - padding, x1 * page_width + padding, y1 * page_height + padding),
        page_width,
        page_height,
    )


def disjoint_cover(rects: list[Rect], width: float, height: float, *, complement: bool) -> list[Rect]:
    """Non-overlapping rectangles covering the union of `rects` (or, with complement, the page minus it).

    WHY: a translucent fill drawn once per rectangle would darken twice where two locations overlap, and the
    vector-PDF output has no mask to fall back on — so overlaps are flattened into disjoint bands first.
    """
    edges = sorted({0.0, float(height), *(r.y0 for r in rects), *(r.y1 for r in rects)})
    edges = [y for y in edges if 0 <= y <= height]
    cover: list[Rect] = []
    for band_top, band_bottom in zip(edges, edges[1:]):
        if band_bottom <= band_top:
            continue
        spans = sorted((r.x0, r.x1) for r in rects if r.y0 < band_bottom and r.y1 > band_top and r.x1 > r.x0)
        merged: list[list[float]] = []
        for left, right in spans:
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        if complement:
            cursor = 0.0
            for left, right in merged:
                if left > cursor:
                    cover.append(Rect(cursor, band_top, left, band_bottom))
                cursor = max(cursor, right)
            if cursor < width:
                cover.append(Rect(cursor, band_top, float(width), band_bottom))
        else:
            cover.extend(Rect(left, band_top, right, band_bottom) for left, right in merged)
    return cover


@dataclass
class Mark:
    """One drawn rectangle, in output pixels."""

    id: str
    page: int
    style: str
    rect: Rect
    label: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "page": self.page, "style": self.style, "rect": self.rect.as_list(), "label": self.label}


@dataclass
class Unresolved:
    """A Location that could not be placed on this source — reported, never silently skipped (CONTRACT §2)."""

    layer: int
    index: int
    location: Any
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"layer": self.layer, "index": self.index, "location": self.location, "reason": self.reason}


@dataclass
class RenderResult:
    outputs: list[str] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outputs": self.outputs,
            "marks": [mark.as_dict() for mark in self.marks],
            "unresolved": [entry.as_dict() for entry in self.unresolved],
        }
