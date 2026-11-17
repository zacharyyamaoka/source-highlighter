"""source_msgs — the shared vocabulary of the source apps (PEP 0001): `Source`, the location selectors, and
`Highlighted` (a source + locations + style). Stdlib only, so markdownify, datasheet-extractor and
source-highlighter can all import it without importing each other.

    src = Source.from_file("datasheet.pdf")            # or from_url(...).capture(), from_text(...), from_bundle(...)
    src.highlight(PageBox(2, 0.61, 0.40, 0.07, 0.02), "box").show()
    src.highlight("page=2&xywh=percent:61,40,7,2").save("marked.png")
"""

from ._source import KINDS, FrozenDict, Source, cache_root
from .highlighted import Highlighted, Layer
from .selectors import (
    SELECTORS,
    Css,
    Note,
    Page,
    PageBox,
    Selector,
    TableCell,
    TextPosition,
    TextQuote,
    Unconvertible,
    XPath,
    as_selector,
    from_location,
    parse,
)

__version__ = "0.1.0"

__all__ = [
    "KINDS",
    "SELECTORS",
    "Css",
    "FrozenDict",
    "Highlighted",
    "Layer",
    "Note",
    "Page",
    "PageBox",
    "Selector",
    "Source",
    "TableCell",
    "TextPosition",
    "TextQuote",
    "Unconvertible",
    "XPath",
    "as_selector",
    "cache_root",
    "from_location",
    "parse",
]
