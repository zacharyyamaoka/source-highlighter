"""Source Highlighter: source + locations + style in, the source highlighted out (CONTRACT.md).

    from source_highlighter import Source, PageBox
    Source.from_file("datasheet.pdf").highlight(PageBox(2, 0.61, 0.40, 0.07, 0.02), "box").show()

`Source`, the selectors and `Highlighted` are source_msgs' (PEP 0001), re-exported so one import does it all.
"""

__version__ = "0.1.0"

from source_msgs import (
    Css,
    Highlighted,
    Note,
    Page,
    PageBox,
    Source,
    TableCell,
    TextPosition,
    TextQuote,
    Unconvertible,
    XPath,
    from_location,
    parse,
)

from .api import highlight
from .geometry import STYLE_DEFAULTS, STYLES, Mark, Rect, RenderResult, Style, Unresolved, normalize_style
from .render import render, source_kind

__all__ = [
    "STYLES",
    "STYLE_DEFAULTS",
    "Css",
    "Highlighted",
    "Mark",
    "Note",
    "Page",
    "PageBox",
    "Rect",
    "RenderResult",
    "Source",
    "Style",
    "TableCell",
    "TextPosition",
    "TextQuote",
    "Unconvertible",
    "Unresolved",
    "XPath",
    "from_location",
    "highlight",
    "normalize_style",
    "parse",
    "render",
    "source_kind",
]
