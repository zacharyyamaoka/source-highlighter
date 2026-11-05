"""Source Highlighter: source + locations + style in, the source highlighted out (CONTRACT.md)."""

__version__ = "0.1.0"

from .geometry import STYLE_DEFAULTS, STYLES, Mark, Rect, RenderResult, Style, Unresolved, normalize_style
from .render import render, source_kind

__all__ = [
    "STYLES",
    "STYLE_DEFAULTS",
    "Mark",
    "Rect",
    "RenderResult",
    "Style",
    "Unresolved",
    "normalize_style",
    "render",
    "source_kind",
]
