"""Highlighted — a Source + locations + style: the highlighter's three inputs as one value (PEP 0001 §5).

W3C Web Annotation calls this a SpecificResource (a source, its selectors, a style class). It is plain data; drawing
it is the highlighter's job, imported only when `.save()` / `.show()` is called, so this module stays stdlib-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .selectors import SELECTORS, Unconvertible, as_selector

if TYPE_CHECKING:
    from ._source import Source

_STYLE_KEYS = ("color", "opacity", "padding", "width", "label")


def _freeze_style(style: Any, options: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    spec = {"name": style} if isinstance(style, str) or style is None else dict(style)
    spec["name"] = spec.get("name") or spec.get("style") or "highlight"
    spec.pop("style", None)
    for key, value in options.items():
        if key not in _STYLE_KEYS:
            raise TypeError(f"unknown style option {key!r} (expected {', '.join(_STYLE_KEYS)})")
        spec[key] = value
    return tuple((key, value) for key, value in spec.items() if value is not None)


def _freeze_locations(locations: Any) -> tuple[Any, ...]:
    if locations is None:
        return ()
    if isinstance(locations, (str, dict, *SELECTORS)):
        locations = [locations]
    return tuple(as_selector(location) for location in locations)


@dataclass(frozen=True, eq=False)
class Layer:
    """Locations drawn in one style."""

    locations: tuple[Any, ...]
    style: tuple[tuple[str, Any], ...]

    @property
    def style_dict(self) -> dict[str, Any]:
        return dict(self.style)


@dataclass(frozen=True, eq=False)
class Highlighted:
    source: Source
    layers: tuple[Layer, ...]

    @classmethod
    def of(cls, source: Source, locations: Any, style: Any = "highlight", **style_options: Any) -> Highlighted:
        if isinstance(locations, dict) and "layers" in locations:
            layers = tuple(Layer(_freeze_locations(layer.get("locations")), _freeze_style(layer.get("style", style), style_options))
                           for layer in locations["layers"])
        elif isinstance(locations, dict) and "locations" in locations and not isinstance(locations.get("locations"), dict):
            layers = (Layer(_freeze_locations(locations["locations"]), _freeze_style(locations.get("style", style), style_options)),)
        else:
            layers = (Layer(_freeze_locations(locations), _freeze_style(style, style_options)),)
        return cls(source, layers)

    def contract_layers(self, kind: str | None = None) -> tuple[list[dict], list[tuple[int, int, Any, str]]]:
        """The CONTRACT §2 form the highlighter draws: [{locations, style}] layers, and every location that cannot
        address this source as (layer, index, location, reason) — for the caller to report, never to drop.
        `kind` overrides the source's kind ('image' when an HTML page is drawn on its snapshot)."""
        kind = kind or self.source.kind
        text: str | None = None
        layers, unconvertible = [], []
        for layer_index, layer in enumerate(self.layers):
            dicts = []
            for index, location in enumerate(layer.locations):
                if isinstance(location, dict):
                    dicts.append(location)
                    continue
                try:
                    if text is None and kind == "text" and self.source.is_saved:
                        text = self.source.text()
                    dicts.append(location.to_location(kind, text))
                except Unconvertible as error:
                    unconvertible.append((layer_index, index, location.compact(), str(error)))
            layers.append({"locations": dicts, "style": layer.style_dict})
        return layers, unconvertible

    @property
    def geometry_only(self) -> bool:
        """Every location is geometry-anchored (PageBox, Page, or a page/box dict): it can be drawn on a picture of
        the source — an HTML page's capture-time snapshot — with no text to anchor in."""
        locations = [location for layer in self.layers for location in layer.locations]
        return bool(locations) and all(
            location.get("page") is not None and not ({"html", "text", "quote", "note"} & set(location))
            if isinstance(location, dict) else location.anchor == "geometry"
            for location in locations
        )

    def save(self, path: str | Path, **render_options: Any):
        """Draw it into `path` (.png/.jpg/.webp; .pdf for a PDF; .html for text) — always headless. Returns the
        highlighter's RenderResult (outputs, marks, unresolved)."""
        from source_highlighter.api import save

        return save(self, path, **render_options)

    def show(self, *, backend: str | None = None, **render_options: Any) -> Path:
        """Draw it and open it, matplotlib-style: the browser (never raised over your window), inline in a notebook,
        or nothing at all (backend 'none', $SOURCE_HIGHLIGHTER_BACKEND=none, no display, or under pytest).
        Returns the page it wrote."""
        from source_highlighter.api import show

        return show(self, backend=backend, **render_options)

    def _repr_png_(self) -> bytes:
        from source_highlighter.api import png_bytes

        return png_bytes(self)

    def __repr__(self) -> str:
        counts = ", ".join(f"{len(layer.locations)} in {layer.style_dict['name']}" for layer in self.layers)
        return f"Highlighted({self.source.kind} {self.source.title or self.source.origin!r}: {counts})"
