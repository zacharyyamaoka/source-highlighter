"""Locations as a small union of selectors (PEP 0001 §6).

Each selector is a frozen dataclass with
- `to_location(kind, text=None)` → the CONTRACT §2 dict the highlighter draws, for a source of that kind
  ('pdf' | 'image' | 'html' | 'text'); `text` is the source's text, needed only where the conversion reads it
  (a table cell or a quote pinned by its context in markdown). What cannot address that kind raises
  `Unconvertible` with the reason — the caller reports it, never drops it;
- `compact()` → a one-line, fragment-style string, borrowed from the standard each selector comes from
  (Text Fragments, RFC 5147, Media Fragments, PDF open parameters); `parse()` reads it back.

Two families (Zach, 2026-11-16): **text-anchored** selectors (`anchor == "text"`: TextQuote, TextPosition, XPath,
Css, TableCell, Note) point into a source's underlying text — HTML, markdown, a PDF's text layer; **geometry-anchored**
ones (`anchor == "geometry"`: PageBox, Page) point at a rendered surface with no text — a photo, a scan, a screenshot.
Turning "this text" into a source + location is a resolver step below the highlighter (the quote find, markdownify's
index.json); these types only carry its answer.

WHY borrowed syntaxes (W3C Web Annotation's FragmentSelector does the same): a quote's compact form pastes straight
onto a live URL after `#:~:text=`, and a page box reads like a PDF/media fragment anyone has seen before.
"""

from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar, Union


class Unconvertible(ValueError):
    """This selector cannot address a source of this kind (or cannot be pinned in this source's text)."""


# Characters a compact value may hold as is. WHY `,`, `-`, `&`, `=` are escaped: they are the grammar.
_SAFE = "/[]()@*:'\"!$+;.~_ #<>|^{}`"


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe=_SAFE.replace(" ", "")).replace(" ", "%20")


def _unquote(value: str) -> str:
    return urllib.parse.unquote(value)


# WHY sha256 only on the geometry selectors (CONTRACT §2): a box is fractions of the page it was measured on, so it
# carries that file's sha256 and is refused on other bytes; text selectors re-find their text and need no pin.
_EXTRA_KEYS = ("id", "label", "sha256")


def _extras(selector: Any) -> dict[str, str]:
    return {key: value for key in _EXTRA_KEYS if (value := getattr(selector, key, None)) is not None}


def _extras_compact(selector: Any) -> str:
    return "".join(f"&{key}={_quote(value)}" for key, value in _extras(selector).items())


def _with_extras(location: dict, selector: Any) -> dict:
    return {**location, **_extras(selector)}


def _check_page(page: Any) -> None:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError(f"page must be an integer from 1 (pages are 1-based, as in PDF.js), got {page!r}")


def _check_sha256(sha256: Any) -> None:
    if sha256 is not None and not (isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256)):
        raise ValueError(f"sha256 must be 64 lowercase hex digits (the file's sha256), got {sha256!r}")


def _check_kind(kind: str) -> None:
    if kind not in ("pdf", "image", "html", "text"):
        raise ValueError(f"kind must be 'pdf', 'image', 'html' or 'text', got {kind!r}")


def _number(value: float) -> str:
    text = format(value, ".7f").rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


# --- the selectors ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TextQuote:
    """The cited text, with optional context (W3C TextQuoteSelector), optionally on one page."""

    anchor: ClassVar[str] = "text"

    exact: str
    prefix: str = ""
    suffix: str = ""
    page: int | None = None
    id: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.exact, str) or not self.exact.strip():
            raise ValueError("TextQuote needs the exact text")
        if self.page is not None:
            _check_page(self.page)

    def to_location(self, kind: str, text: str | None = None) -> dict:
        _check_kind(kind)
        if kind == "image":
            raise Unconvertible("an image has no text to find a quote in; give a PageBox")
        if kind == "html":
            quote = {"exact": self.exact, **({"prefix": self.prefix} if self.prefix else {}),
                     **({"suffix": self.suffix} if self.suffix else {})}
            return _with_extras({"html": {"text_quote": quote}}, self)
        if kind == "text" and (self.prefix or self.suffix):
            # WHY pinned here: the {quote} Location carries no context, so a quote whose context makes it unique is
            # turned into the exact span — the same "exactly once, else report" rule as CONTRACT §2.
            if text is None:
                raise Unconvertible("pinning a quote by its prefix/suffix needs the source's text")
            needle = self.prefix + self.exact + self.suffix
            starts = [match.start() for match in re.finditer(re.escape(needle), text)]
            if not starts:
                raise Unconvertible(f'"{self.exact}" with that prefix/suffix is not in the source')
            if len(starts) > 1:
                raise Unconvertible(f'ambiguous: "{self.exact}" with that prefix/suffix occurs {len(starts)} times')
            start = starts[0] + len(self.prefix)
            return _with_extras({"text": {"start": start, "end": start + len(self.exact)}}, self)
        location = {"quote": self.exact} if self.page is None else {"page": self.page, "quote": self.exact}
        return _with_extras(location, self)

    def compact(self) -> str:
        """Text Fragments syntax: `text=[prefix-,]exact[,-suffix]`."""
        parts = ([f"{_quote(self.prefix)}-"] if self.prefix else []) + [_quote(self.exact)]
        parts += [f"-{_quote(self.suffix)}"] if self.suffix else []
        page = f"page={self.page}&" if self.page is not None else ""
        return f"{page}text={','.join(parts)}{_extras_compact(self)}"


@dataclass(frozen=True)
class TextPosition:
    """Code points [start, end) into the source's decoded text (W3C TextPositionSelector)."""

    anchor: ClassVar[str] = "text"

    start: int
    end: int
    id: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if not (isinstance(self.start, int) and isinstance(self.end, int) and 0 <= self.start < self.end):
            raise ValueError(f"TextPosition needs 0 <= start < end, got {self.start}, {self.end}")

    def to_location(self, kind: str, text: str | None = None) -> dict:
        _check_kind(kind)
        span = {"start": self.start, "end": self.end}
        if kind == "text":
            return _with_extras({"text": span}, self)
        if kind == "html":
            return _with_extras({"html": {"source_range": span}}, self)
        raise Unconvertible(f"a text position counts into text; a{'n' if kind == 'image' else ''} {kind} needs a PageBox")

    def compact(self) -> str:
        """RFC 5147 (text/plain fragments): `char=start,end`."""
        return f"char={self.start},{self.end}{_extras_compact(self)}"


@dataclass(frozen=True)
class PageBox:
    """A rectangle on a page: x, y from the top-left, w, h — each 0–1 of the page's width/height.

    WHY fractions, not points: the same box holds at any zoom, on a PDF page or on its screenshot.
    """

    anchor: ClassVar[str] = "geometry"

    page: int
    x: float
    y: float
    w: float
    h: float
    id: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)
    sha256: str | None = field(default=None, kw_only=True)  # of the file it was measured on: refused on other bytes

    def __post_init__(self) -> None:
        _check_page(self.page)
        _check_sha256(self.sha256)
        for name in ("x", "y", "w", "h"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"PageBox.{name} must be a finite number, got {value!r}")
            # WHY rounded: x1 - x0 of two fixture numbers is 0.06423000000000001; nine places is far below a pixel.
            object.__setattr__(self, name, round(float(value), 9))
        if self.w <= 0 or self.h <= 0 or self.x < 0 or self.y < 0 or self.x + self.w > 1 + 1e-9 or self.y + self.h > 1 + 1e-9:
            raise ValueError(f"PageBox must lie on the page (0–1, w and h > 0), got x={self.x} y={self.y} w={self.w} h={self.h}")

    @property
    def box(self) -> list[float]:
        """CONTRACT §2's [x0, y0, x1, y1]."""
        return [self.x, self.y, round(self.x + self.w, 9), round(self.y + self.h, 9)]

    def to_location(self, kind: str, text: str | None = None) -> dict:
        _check_kind(kind)
        if kind in ("pdf", "image"):
            return _with_extras({"page": self.page, "box": self.box}, self)
        raise Unconvertible(
            f"a page box addresses a PDF page or an image, not {kind}"
            + ("; an HTML source draws it on its stored snapshot when the capture kept one" if kind == "html" else "")
        )

    def compact(self) -> str:
        """Media Fragments `xywh=percent:x,y,w,h` (decimals allowed) on PDF open parameters' `page=N`."""
        numbers = ",".join(_number(value * 100) for value in (self.x, self.y, self.w, self.h))
        return f"page={self.page}&xywh=percent:{numbers}{_extras_compact(self)}"


@dataclass(frozen=True)
class Page:
    """A whole page (a slide, a drawing): shown and outlined, no finer mark."""

    anchor: ClassVar[str] = "geometry"

    page: int
    id: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)
    sha256: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _check_page(self.page)
        _check_sha256(self.sha256)

    def to_location(self, kind: str, text: str | None = None) -> dict:
        _check_kind(kind)
        if kind in ("pdf", "image"):
            return _with_extras({"page": self.page}, self)
        raise Unconvertible(f"a page addresses a PDF or an image, not {kind}")

    def compact(self) -> str:
        return f"page={self.page}{_extras_compact(self)}"


def _html_only(kind: str, what: str) -> None:
    _check_kind(kind)
    if kind != "html":
        raise Unconvertible(f"{what} addresses an HTML page's DOM, not {kind}")


@dataclass(frozen=True)
class XPath:
    """An element by XPath (W3C XPathSelector)."""

    anchor: ClassVar[str] = "text"

    path: str
    id: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("XPath needs a path")

    def to_location(self, kind: str, text: str | None = None) -> dict:
        _html_only(kind, "an XPath")
        return _with_extras({"html": {"xpath": self.path}}, self)

    def compact(self) -> str:
        return f"xpath={_quote(self.path)}{_extras_compact(self)}"


@dataclass(frozen=True)
class Css:
    """An element by CSS selector (W3C CssSelector). It must match exactly one element, else it is reported."""

    anchor: ClassVar[str] = "text"

    selector: str
    id: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.selector, str) or not self.selector.strip():
            raise ValueError("Css needs a selector")

    def to_location(self, kind: str, text: str | None = None) -> dict:
        _html_only(kind, "a CSS selector")
        return _with_extras({"html": {"css": self.selector}}, self)

    def compact(self) -> str:
        return f"css={_quote(self.selector)}{_extras_compact(self)}"


@dataclass(frozen=True)
class TableCell:
    """The cell at `row`, `col` of the `table`-th table, all 1-based; row 1 is the table's first row (its header
    row, when it has one)."""

    anchor: ClassVar[str] = "text"

    row: int
    col: int
    table: int = 1
    id: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        for name in ("row", "col", "table"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"TableCell.{name} is 1-based, got {value!r}")

    def to_location(self, kind: str, text: str | None = None) -> dict:
        _check_kind(kind)
        if kind == "html":
            # WHY descendant::tr: it counts rows through <thead>/<tbody> the parser inserts, as a reader would.
            xpath = f"(//table)[{self.table}]/descendant::tr[{self.row}]/*[self::td or self::th][{self.col}]"
            return _with_extras({"html": {"xpath": xpath}}, self)
        if kind == "text":
            if text is None:
                raise Unconvertible("a table cell in markdown needs the source's text")
            start, end = gfm_table_cell(text, self.table, self.row, self.col)
            return _with_extras({"text": {"start": start, "end": end}}, self)
        raise Unconvertible(
            f"a table cell on a{'n' if kind == 'image' else ''} {kind} needs its box: markdownify's index.json gives "
            "every table_cell block a page + box (use a PageBox)"
        )

    def compact(self) -> str:
        return f"table={self.table}&row={self.row}&col={self.col}{_extras_compact(self)}"


@dataclass(frozen=True)
class Note:
    """A markdown / vault note's heading and/or ^block id (CONTRACT §2 `note`)."""

    anchor: ClassVar[str] = "text"

    heading: str | None = None
    block: str | None = None
    id: str | None = field(default=None, kw_only=True)
    label: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if not self.heading and not self.block:
            raise ValueError("Note needs a heading or a block id")

    def to_location(self, kind: str, text: str | None = None) -> dict:
        _check_kind(kind)
        if kind != "text":
            raise Unconvertible(f"a note heading/block addresses a markdown note, not {kind}")
        note = {key: value for key in ("heading", "block") if (value := getattr(self, key))}
        return _with_extras({"note": note}, self)

    def compact(self) -> str:
        parts = [f"{key}={_quote(value)}" for key in ("heading", "block") if (value := getattr(self, key))]
        return "&".join(parts) + _extras_compact(self)


Selector = Union[TextQuote, TextPosition, PageBox, Page, XPath, Css, TableCell, Note]
SELECTORS = (TextQuote, TextPosition, PageBox, Page, XPath, Css, TableCell, Note)


# --- markdown tables ---------------------------------------------------------------------------------------------

_DELIMITER = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _cells(line: str, line_start: int) -> list[tuple[int, int]]:
    """Each cell's stripped content as (start, end) offsets into the whole text. `\\|` does not split."""
    bounds = [index for index, char in enumerate(line) if char == "|" and (index == 0 or line[index - 1] != "\\")]
    stripped = line.strip()
    if not stripped.startswith("|"):
        bounds = [-1, *bounds]
    if not stripped.endswith("|") or (bounds and bounds[-1] != len(line.rstrip()) - 1):
        bounds = [*bounds, len(line.rstrip())]
    cells = []
    for left, right in zip(bounds, bounds[1:]):
        start, end = left + 1, right
        while start < end and line[start].isspace():
            start += 1
        while end > start and line[end - 1].isspace():
            end -= 1
        cells.append((line_start + start, line_start + end))
    return cells


def gfm_table_cell(text: str, table: int, row: int, col: int) -> tuple[int, int]:
    """The code-point span of a GFM pipe-table cell's content (1-based table/row/col; row 1 = header row)."""
    lines = text.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    found = 0
    index = 0
    while index < len(lines) - 1:
        if "|" in lines[index] and _DELIMITER.match(lines[index + 1]) and "-" in lines[index + 1]:
            found += 1
            rows = [index]
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].strip() and "|" in lines[cursor]:
                rows.append(cursor)
                cursor += 1
            if found == table:
                if row > len(rows):
                    raise Unconvertible(f"table {table} has {len(rows)} rows; there is no row {row}")
                cells = _cells(lines[rows[row - 1]], offsets[rows[row - 1]])
                if col > len(cells):
                    raise Unconvertible(f"row {row} of table {table} has {len(cells)} cells; there is no column {col}")
                start, end = cells[col - 1]
                if start == end:
                    raise Unconvertible(f"cell row {row}, column {col} of table {table} is empty")
                return start, end
            index = cursor
            continue
        index += 1
    raise Unconvertible(f"the source has {found} markdown table{'s' if found != 1 else ''}; there is no table {table}")


# --- compact strings and CONTRACT §2 dicts back to selectors -----------------------------------------------------


def _int(value: str, name: str) -> int:
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"{name} must be a whole number, got {value!r}")
    return int(value)


def parse(text: str) -> Selector:
    """A compact string (any selector's `.compact()`) back to its selector."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("an empty compact location")
    pairs: dict[str, str] = {}
    for part in text.strip().lstrip("#").split("&"):
        key, sep, value = part.partition("=")
        if not sep or key in pairs:
            raise ValueError(f"cannot read {text!r}: expected key=value pairs joined by &")
        pairs[key] = value
    extras = {key: _unquote(pairs.pop(key)) for key in ("id", "label") if key in pairs}
    pin = {"sha256": pairs.pop("sha256")} if "sha256" in pairs else {}
    keys = set(pairs)
    if pin and keys not in ({"page", "xywh"}, {"page"}):
        raise ValueError(f"cannot read {text!r}: sha256= pins a geometry selector (page, page + xywh) only")
    if "text" in keys and keys <= {"text", "page"}:
        tokens = pairs["text"].split(",")  # split before unquoting: an escaped %2C is part of the text
        prefix = suffix = ""
        if tokens and tokens[0].endswith("-") and len(tokens) > 1:
            prefix = _unquote(tokens.pop(0)[:-1])
        if tokens and tokens[-1].startswith("-") and len(tokens) > 1:
            suffix = _unquote(tokens.pop()[1:])
        if len(tokens) != 1:
            raise ValueError(f"cannot read {text!r}: a text= quote holds one exact text (a start,end range is not supported)")
        page = _int(pairs["page"], "page") if "page" in pairs else None
        return TextQuote(_unquote(tokens[0]), prefix, suffix, page, **extras)
    if keys == {"char"}:
        match = re.fullmatch(r"(\d+),(\d+)", pairs["char"])
        if not match:
            raise ValueError(f"cannot read {text!r}: char= needs start,end")
        return TextPosition(int(match[1]), int(match[2]), **extras)
    if keys == {"page", "xywh"}:
        match = re.fullmatch(r"percent:([\d.]+),([\d.]+),([\d.]+),([\d.]+)", pairs["xywh"])
        if not match:
            raise ValueError(f"cannot read {text!r}: xywh= needs percent:x,y,w,h")
        x, y, w, h = (float(value) / 100 for value in match.groups())
        return PageBox(_int(pairs["page"], "page"), x, y, w, h, **extras, **pin)
    if keys == {"page"}:
        return Page(_int(pairs["page"], "page"), **extras, **pin)
    if keys == {"xpath"}:
        return XPath(_unquote(pairs["xpath"]), **extras)
    if keys == {"css"}:
        return Css(_unquote(pairs["css"]), **extras)
    if keys == {"table", "row", "col"}:
        return TableCell(_int(pairs["row"], "row"), _int(pairs["col"], "col"), _int(pairs["table"], "table"), **extras)
    if keys and keys <= {"heading", "block"}:
        return Note(**{key: _unquote(value) for key, value in pairs.items()}, **extras)
    raise ValueError(f"cannot read {text!r}: unknown keys {', '.join(sorted(keys))}")


def from_location(location: dict) -> Selector:
    """A CONTRACT §2 dict that names one selector back to it. An html Location carrying several (source_range +
    xpath + text_quote, the resolver's fallback chain) has no single selector: pass the dict itself instead."""
    if not isinstance(location, dict):
        raise ValueError(f"a Location is a dict, got {type(location).__name__}")
    extras = {key: location[key] for key in ("id", "label") if location.get(key) is not None}
    pin = {"sha256": location["sha256"]} if location.get("sha256") is not None else {}
    if "box" in location:
        x0, y0, x1, y1 = location["box"]
        return PageBox(location.get("page", 1), x0, y0, x1 - x0, y1 - y0, **extras, **pin)
    if "text" in location:
        return TextPosition(location["text"]["start"], location["text"]["end"], **extras)
    if "quote" in location:
        return TextQuote(location["quote"], page=location.get("page"), **extras)
    if "note" in location:
        return Note(location["note"].get("heading"), location["note"].get("block"), **extras)
    if "html" in location:
        html = location["html"]
        present = [key for key in ("source_range", "xpath", "css", "text_quote") if html.get(key)]
        if len(present) != 1:
            raise ValueError(f"this html Location holds several selectors ({', '.join(present)}); pass the dict itself")
        key = present[0]
        if key == "source_range":
            return TextPosition(html[key]["start"], html[key]["end"], **extras)
        if key == "xpath":
            return XPath(html[key], **extras)
        if key == "css":
            return Css(html[key], **extras)
        quote = html[key]
        return TextQuote(quote["exact"], quote.get("prefix", ""), quote.get("suffix", ""), **extras)
    if "page" in location:
        return Page(location["page"], **extras, **pin)
    raise ValueError(f"not a CONTRACT §2 Location: {location!r}")


def as_selector(value: Any) -> Selector | dict:
    """A selector as given, a compact string parsed, a dict kept as the CONTRACT §2 Location it is."""
    if isinstance(value, (*SELECTORS, dict)):
        return value
    if isinstance(value, str):
        return parse(value)
    raise TypeError(f"a location is a selector, a compact string or a CONTRACT §2 dict, got {type(value).__name__}")


def selector_fields(selector: Selector) -> dict[str, Any]:
    return {item.name: getattr(selector, item.name) for item in fields(selector)}
