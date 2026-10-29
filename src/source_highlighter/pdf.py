"""PDF pages in and out with pypdfium2 (Apache-2.0 / BSD) — no AGPL PyMuPDF in the core."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from PIL import Image

from .geometry import Rect, Style, disjoint_cover
from .paint import rgba


def page_sizes(document: pdfium.PdfDocument) -> list[tuple[float, float]]:
    """Each page's displayed size in points (rotation applied) — the W and H a page/box Location scales by."""
    sizes = []
    for index in range(len(document)):
        page = document[index]
        width, height = page.get_size()
        if page.get_rotation() in (90, 270):
            width, height = height, width
        sizes.append((width, height))
    return sizes


def render_page(document: pdfium.PdfDocument, page_number: int, scale: float) -> Image.Image:
    # WHY no annotations: the page as printed, without viewer-only widgets drawn on top.
    return document[page_number - 1].render(scale=scale, draw_annots=False).to_pil().convert("RGB")


def _insert_path(page: pdfium.PdfPage, rects: list[Rect], color: tuple[int, int, int, int], *, even_odd: bool) -> None:
    """Add one filled vector path made of `rects` (displayed points, origin top-left).

    WHY one path, not one object per rectangle: abutting objects each antialias their shared edge, which
    shows as hairline seams through a veil; subpaths of one path are rasterised together, seam-free.
    """
    left, _bottom, _right, top = page.get_cropbox()
    rects = [rect for rect in rects if rect.x1 > rect.x0 and rect.y1 > rect.y0]
    if not rects:
        return
    path = None
    for rect in rects:
        # PDF user space has its origin bottom-left; the Location's box has it top-left.
        corners = [(rect.x0, rect.y0), (rect.x1, rect.y0), (rect.x1, rect.y1), (rect.x0, rect.y1)]
        corners = [(left + x, top - y) for x, y in corners]
        if path is None:
            path = pdfium_raw.FPDFPageObj_CreateNewPath(*corners[0])
        else:
            pdfium_raw.FPDFPath_MoveTo(path, *corners[0])
        for corner in corners[1:]:
            pdfium_raw.FPDFPath_LineTo(path, *corner)
        pdfium_raw.FPDFPath_Close(path)
    pdfium_raw.FPDFPageObj_SetFillColor(path, *color)
    fill_mode = pdfium_raw.FPDF_FILLMODE_ALTERNATE if even_odd else pdfium_raw.FPDF_FILLMODE_WINDING
    pdfium_raw.FPDFPath_SetDrawMode(path, fill_mode, False)
    pdfium_raw.FPDFPage_InsertObject(page.raw, path)


def add_vector_marks(page: pdfium.PdfPage, page_size: tuple[float, float], rects: list[Rect], style: Style) -> None:
    """Draw one style's marks onto a PDF page as vector objects (rects in displayed points).

    Blackout here hides, it does not redact: the covered text is still in the content stream.
    """
    width, height = page_size
    paint = rgba(style.color, style.opacity)
    if style.name == "highlight":
        _insert_path(page, disjoint_cover(rects, width, height, complement=False), paint, even_odd=False)
    elif style.name in ("spotlight", "blackout"):
        # The page, minus the union of the locations: even-odd over the page rectangle and disjoint holes.
        holes = disjoint_cover(rects, width, height, complement=False)
        _insert_path(page, [Rect(0, 0, width, height), *holes], paint, even_odd=True)
    elif style.name == "underline":
        bars = [Rect(rect.x0, rect.y1, rect.x1, rect.y1 + style.width) for rect in rects]
        _insert_path(page, disjoint_cover(bars, width, height, complement=False), paint, even_odd=False)
    else:  # box
        sides = []
        for rect in rects:
            stroke = style.width
            outer = Rect(rect.x0 - stroke, rect.y0 - stroke, rect.x1 + stroke, rect.y1 + stroke)
            sides += [
                Rect(outer.x0, outer.y0, outer.x1, rect.y0),
                Rect(outer.x0, rect.y1, outer.x1, outer.y1),
                Rect(outer.x0, rect.y0, rect.x0, rect.y1),
                Rect(rect.x1, rect.y0, outer.x1, rect.y1),
            ]
        _insert_path(page, disjoint_cover(sides, width, height, complement=False), paint, even_odd=False)


def save_marked_pdf(
    source_path: Path, layers: list[tuple[Style, dict[int, list[Rect]]]], out_path: Path
) -> list[int]:
    """Write a copy of the PDF with every layer's marks as vector objects (rects in displayed points).

    Returns the page numbers that could not be marked: v0 maps boxes to PDF user space for unrotated pages only.
    """
    document = pdfium.PdfDocument(str(source_path))
    sizes = page_sizes(document)
    # WHY one handle per page: each FPDF_LoadPage is a fresh page object, so objects inserted through one
    # handle are invisible to GenerateContent called through another.
    handles: dict[int, pdfium.PdfPage] = {}
    touched: set[int] = set()
    skipped: set[int] = set()
    for style, rects_by_page in layers:
        # A spotlight or blackout with no location on a page still covers that whole page.
        pages = range(1, len(document) + 1) if style.name in ("spotlight", "blackout") else sorted(rects_by_page)
        for page_number in pages:
            page = handles.setdefault(page_number, document[page_number - 1])
            if page.get_rotation() != 0:
                skipped.add(page_number)
                continue
            add_vector_marks(page, sizes[page_number - 1], rects_by_page.get(page_number, []), style)
            touched.add(page_number)
    for page_number in touched:
        pdfium_raw.FPDFPage_GenerateContent(handles[page_number].raw)
    document.save(str(out_path))
    document.close()
    return sorted(skipped)
