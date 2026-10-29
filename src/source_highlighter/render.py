"""The static form (CONTRACT §4.1): burn the marks into a copy of the source."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

from .browser import render_in_browser, write_text_page
from .geometry import (
    Layer,
    Mark,
    Rect,
    RenderResult,
    Style,
    Unresolved,
    is_valid_box,
    location_kind,
    normalize_layers,
    page_box_rect,
)
from .paint import paint_layer

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
DEFAULT_SCALE = {"pdf": 2.0, "image": 1.0, "html": 1.0, "text": 1.0}
DEFAULT_WIDTH = {"html": 1280, "text": 900}  # html: the width the visual twins were captured at


def source_kind(path: Path) -> str:
    """'pdf' | 'image' | 'html' | 'text', from the bytes first.

    WHY sniff: cached sources (e.g. ~/.cache/robotparts-ingest/blobs/<sha256>) have no file extension.
    """
    head = path.read_bytes()[:512]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM")) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return "image"
    if path.suffix.lower() in HTML_SUFFIXES or re.match(rb"\s*(<!doctype html|<html)", head, re.IGNORECASE):
        return "html"
    return "text"


def decode_html(raw: bytes) -> str:
    """The decoded HTML string that html source_range offsets count code points into."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        declared = re.search(rb"charset=[\"']?([A-Za-z0-9_\-]+)", raw[:4096])
        return raw.decode(declared.group(1).decode() if declared else "latin-1", errors="replace")


def _load_locations(locations: Any) -> Any:
    if isinstance(locations, (str, Path)):
        return json.loads(Path(locations).read_text(encoding="utf-8"))
    return locations


def _resolve_page_boxes(
    layers: list[Layer],
    page_count: int,
    page_sizes: list[tuple[float, float]],
    source_label: str,
    result: RenderResult,
) -> list[tuple[Style, dict[int, list[tuple[Rect, str | None, str]]]]]:
    """Page/box Locations → rectangles in source units, per layer and page; the rest go to result.unresolved."""
    resolved_layers = []
    for layer_index, layer in enumerate(layers):
        by_page: dict[int, list[tuple[Rect, str | None, str]]] = {}
        for index, location in enumerate(layer.locations):
            kind = location_kind(location)
            reason = None
            if kind is None:
                reason = 'a Location needs exactly one of "box" (with "page"), "html" or "text"'
            elif kind != "page":
                reason = f"a{'n' if kind == 'html' else ''} {kind} Location cannot address {source_label}"
            elif not is_valid_box(location["box"]):
                reason = "box must be [x0, y0, x1, y1] within 0–1 with x0 < x1 and y0 < y1"
            else:
                page_number = location.get("page", 1)
                if not isinstance(page_number, int) or not 1 <= page_number <= page_count:
                    reason = f"page {page_number} does not exist (the source has {page_count})"
            if reason:
                result.unresolved.append(Unresolved(layer_index, index, location, reason))
                continue
            page_number = location.get("page", 1)
            width, height = page_sizes[page_number - 1]
            rect = page_box_rect(location["box"], width, height, layer.style.padding)
            location_id = str(location.get("id", f"{layer_index}:{index}"))
            by_page.setdefault(page_number, []).append((rect, location.get("label"), location_id))
        resolved_layers.append((layer.style, by_page))
    return resolved_layers


def _paint_page(image: Image.Image, page_number: int, resolved_layers, scale: float, result: RenderResult) -> Image.Image:
    for style, by_page in resolved_layers:
        entries = by_page.get(page_number, [])
        if not entries and style.name not in ("spotlight", "blackout"):
            continue
        pixel_rects = [(rect.scaled(scale), label) for rect, label, _ in entries]
        image = paint_layer(image, pixel_rects, style, scale)
        for (rect, label, location_id), (pixel_rect, _) in zip(entries, pixel_rects):
            result.marks.append(Mark(location_id, page_number, style.name, pixel_rect, label))
    return image


def _save_image(image: Image.Image, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() in (".jpg", ".jpeg"):
        image.convert("RGB").save(out_path, quality=92)
    else:
        image.save(out_path)


def _render_pdf(source_path, layers, out_path, page, scale, pdf_out, result) -> None:
    import pypdfium2 as pdfium

    from .pdf import page_sizes, render_page, save_marked_pdf

    document = pdfium.PdfDocument(str(source_path))
    sizes = page_sizes(document)
    resolved_layers = _resolve_page_boxes(layers, len(document), sizes, "a PDF source", result)
    wants_pdf = out_path.suffix.lower() == ".pdf"
    if pdf_out or wants_pdf:
        target = out_path if wants_pdf else Path(pdf_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        vector_layers = [(style, {number: [rect for rect, _, _ in entries] for number, entries in by_page.items()}) for style, by_page in resolved_layers]
        for skipped_page in save_marked_pdf(Path(source_path), vector_layers, target):
            result.unresolved.append(Unresolved(-1, -1, {"page": skipped_page}, "rotated page: v0 marks it in PNG output only, not in the PDF"))
        result.outputs.append(str(target))
        if wants_pdf:
            for style, by_page in resolved_layers:
                for number, entries in by_page.items():
                    result.marks.extend(Mark(location_id, number, style.name, rect, label) for rect, label, location_id in entries)
            document.close()
            return
    if page is not None:
        if not 1 <= page <= len(document):
            document.close()
            raise ValueError(f"--page {page} is outside the document (1–{len(document)})")
        pages = [page]
    else:
        pages = sorted({number for _, by_page in resolved_layers for number in by_page}) or [1]
    for number in pages:
        image = _paint_page(render_page(document, number, scale), number, resolved_layers, scale, result)
        target = out_path if len(pages) == 1 else out_path.with_name(f"{out_path.stem}-p{number}{out_path.suffix}")
        _save_image(image, target)
        result.outputs.append(str(target))
    document.close()


def _render_image(source_path, layers, out_path, scale, result, *, twin: bool) -> None:
    image = Image.open(source_path)
    image.load()
    image = image.convert("RGB")
    if scale != 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    natural_size = (image.width / scale, image.height / scale)
    label = "a visual twin (a screenshot has no DOM; give page/box Locations for the twin)" if twin else "an image source"
    resolved_layers = _resolve_page_boxes(layers, 1, [natural_size], label, result)
    image = _paint_page(image, 1, resolved_layers, scale, result)
    _save_image(image, out_path)
    result.outputs.append(str(out_path))


def _render_browser(kind, source_path, layers, out_path, scale, width, result) -> None:
    raw = Path(source_path).read_bytes()
    source_text = decode_html(raw) if kind == "html" else raw.decode("utf-8")
    if kind == "text" and out_path.suffix.lower() in (".html", ".htm"):
        write_text_page(source_text, layers, width=width, out_html=out_path)
        result.outputs.append(str(out_path))
        # The marks of an HTML page are laid out by whoever opens it; resolve here only to report problems.
        scratch = out_path.with_suffix(".resolve.png")
        resolved = render_in_browser(kind, source_text, layers, width=width, scale=1, out_png=scratch)
        scratch.unlink(missing_ok=True)
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        resolved = render_in_browser(kind, source_text, layers, width=width, scale=scale, out_png=out_path)
        result.outputs.append(str(out_path))
    for mark in resolved["marks"]:
        rect = Rect(**mark["rect"]).scaled(scale)
        result.marks.append(Mark(str(mark["id"]), mark["page"], mark["style"], rect, mark.get("label")))
    for entry in resolved["unresolved"]:
        result.unresolved.append(Unresolved(entry["layer"], entry["index"], entry["location"], entry["reason"]))


def render(
    source_path: str | Path,
    locations: Any,
    style: str | dict | Style | None = "highlight",
    out_path: str | Path = "marked.png",
    *,
    page: int | None = None,
    scale: float | None = None,
    twin: str | Path | None = None,
    pdf_out: str | Path | None = None,
    width: int | None = None,
) -> RenderResult:
    """Burn `locations` in `style` into a copy of `source_path`, written to `out_path`.

    locations: a list of Locations (CONTRACT §2), {"locations": [...]}, {"layers": [{locations, style}]}, or a
      path to a JSON file holding any of those.
    PDF → PNG page(s) (several pages get `-pN` suffixes) or, with a .pdf out_path / pdf_out, a vector-marked PDF.
    Image → image. HTML → PNG of the page laid out by headless Chromium, or of its visual `twin` (an image)
    when given. Text/markdown → PNG, or a self-contained .html page.
    scale: output pixels per source unit (PDF default 2 = 144 dpi). width: layout width for HTML/text.
    Locations that cannot be placed are listed in result.unresolved, never dropped silently.
    """
    source_path = Path(source_path)
    out_path = Path(out_path)
    layers = normalize_layers(_load_locations(locations), style)
    result = RenderResult()
    kind = source_kind(source_path)
    if twin is not None:
        if kind != "html":
            raise ValueError("a visual twin is only meaningful for an HTML source")
        _render_image(Path(twin), layers, out_path, scale or 1.0, result, twin=True)
    elif kind == "pdf":
        _render_pdf(source_path, layers, out_path, page, scale or DEFAULT_SCALE["pdf"], pdf_out, result)
    elif kind == "image":
        _render_image(source_path, layers, out_path, scale or 1.0, result, twin=False)
    else:
        _render_browser(kind, source_path, layers, out_path, scale or 1.0, width or DEFAULT_WIDTH[kind], result)
    return result
