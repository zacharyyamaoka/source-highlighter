"""The static form, measured pixel by pixel against the unmarked source.

Every check predicts the exact pixel value from the original and the style:
  highlight inside  = original * (1 - a) + colour * a
  spotlight outside = original * (1 - a)
  blackout  outside = 0, inside = original
"""

from __future__ import annotations

import json

import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageColor

from source_highlighter import STYLE_DEFAULTS, render
from source_highlighter.cli import main as cli_main

from .conftest import (
    CUBEMARS_HTML,
    CUBEMARS_MD,
    CUBEMARS_TWIN_JPG,
    CUBEMARS_TWIN_LOCATIONS,
    EROB_JPG,
    EROB_LOCATIONS,
    J60_PDF,
    inside_mask,
    load_locations,
    pixels,
    touching_mask,
)

PDF_SCALE = 2.0


def alpha(opacity: float) -> float:
    return round(opacity * 255) / 255  # 8-bit alpha, as composited


def original_pdf_page(page_number: int) -> np.ndarray:
    document = pdfium.PdfDocument(str(J60_PDF))
    image = document[page_number - 1].render(scale=PDF_SCALE, draw_annots=False).to_pil()
    document.close()
    return pixels(image)


def mark_rects(result, page: int = 1) -> list[list[float]]:
    return [mark.rect.as_list() for mark in result.marks if mark.page == page]


# ---- PDF (J60 Joint datasheet, page 2) -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def j60_original():
    return original_pdf_page(2)


def render_j60(tmp_path, style):
    out = tmp_path / f"j60-{style}.png"
    result = render(J60_PDF, load_locations("j60-joint.locations.json"), style, out, scale=PDF_SCALE)
    assert result.unresolved == []
    assert result.outputs == [str(out)]
    return result, pixels(out)


def test_pdf_highlight_pixels_carry_the_highlight_colour(tmp_path, j60_original):
    result, marked = render_j60(tmp_path, "highlight")
    rects = mark_rects(result, page=2)
    assert len(rects) == 4
    a = alpha(STYLE_DEFAULTS["highlight"]["opacity"])
    colour = np.array(ImageColor.getrgb(STYLE_DEFAULTS["highlight"]["color"]))
    inside = inside_mask(marked.shape, rects)
    expected = np.round(j60_original * (1 - a) + colour * a)
    error_inside = np.abs(marked[inside] - expected[inside]).max()
    outside = ~touching_mask(marked.shape, rects)
    error_outside = np.abs(marked[outside] - j60_original[outside]).max()
    print(f"highlight: {inside.sum()} px inside, max error {error_inside}; outside max change {error_outside}")
    assert error_inside <= 1
    assert error_outside == 0


def test_pdf_spotlight_dims_everything_else_by_the_expected_factor(tmp_path, j60_original):
    result, marked = render_j60(tmp_path, "spotlight")
    rects = mark_rects(result, page=2)
    a = alpha(STYLE_DEFAULTS["spotlight"]["opacity"])
    outside = ~touching_mask(marked.shape, rects)
    expected = np.round(j60_original * (1 - a))
    error_outside = np.abs(marked[outside] - expected[outside]).max()
    bright = outside & (j60_original.min(axis=2) > 100)
    measured_factor = (marked[bright] / j60_original[bright]).mean()
    inside = inside_mask(marked.shape, rects)
    error_inside = np.abs(marked[inside] - j60_original[inside]).max()
    print(f"spotlight: outside factor {measured_factor:.3f} (expected {1 - a:.3f}), max error {error_outside}; inside max change {error_inside}")
    assert error_outside <= 1
    assert abs(measured_factor - (1 - a)) < 0.01
    assert error_inside == 0


def test_pdf_blackout_is_black_outside_and_original_inside(tmp_path, j60_original):
    result, marked = render_j60(tmp_path, "blackout")
    rects = mark_rects(result, page=2)
    outside = ~touching_mask(marked.shape, rects)
    inside = inside_mask(marked.shape, rects)
    print(f"blackout: outside max {marked[outside].max()}, inside max change {np.abs(marked[inside] - j60_original[inside]).max()}")
    assert marked[outside].max() == 0
    assert np.array_equal(marked[inside], j60_original[inside])


def test_pdf_box_draws_an_outline_just_outside_each_location(tmp_path, j60_original):
    result, marked = render_j60(tmp_path, "box")
    rects = mark_rects(result, page=2)
    stroke = STYLE_DEFAULTS["box"]["width"] * PDF_SCALE
    colour = np.array(ImageColor.getrgb(STYLE_DEFAULTS["box"]["color"]))
    band = inside_mask(marked.shape, rects, grow=stroke) & ~touching_mask(marked.shape, rects)
    inside = inside_mask(marked.shape, rects)
    assert band.sum() > 0
    assert np.abs(marked[band] - colour).max() == 0
    assert np.array_equal(marked[inside], j60_original[inside])


def test_pdf_underline_draws_a_bar_below_each_location(tmp_path, j60_original):
    result, marked = render_j60(tmp_path, "underline")
    rects = mark_rects(result, page=2)
    stroke = STYLE_DEFAULTS["underline"]["width"] * PDF_SCALE
    colour = np.array(ImageColor.getrgb(STYLE_DEFAULTS["underline"]["color"]))
    bars = [[x0, y1, x1, y1 + stroke] for x0, y0, x1, y1 in rects]
    under = inside_mask(marked.shape, bars)
    assert under.sum() > 0
    assert np.abs(marked[under] - colour).max() == 0
    assert np.array_equal(marked[inside_mask(marked.shape, rects)], j60_original[inside_mask(marked.shape, rects)])


def test_pdf_vector_output_matches_the_png(tmp_path, j60_original):
    """The marked PDF, rendered back, shows the same spotlight as the PNG."""
    out_pdf = tmp_path / "j60-spotlight.pdf"
    result = render(J60_PDF, load_locations("j60-joint.locations.json"), "spotlight", out_pdf)
    assert result.outputs == [str(out_pdf)]
    document = pdfium.PdfDocument(str(out_pdf))
    assert len(document) == 2
    marked = pixels(document[1].render(scale=PDF_SCALE, draw_annots=False).to_pil())
    rects = [mark.rect.scaled(PDF_SCALE).as_list() for mark in result.marks if mark.page == 2]
    a = alpha(STYLE_DEFAULTS["spotlight"]["opacity"])
    outside = ~touching_mask(marked.shape, rects, grow=1)
    inside = inside_mask(marked.shape, rects, grow=-1)
    error_outside = np.abs(marked[outside] - np.round(j60_original[outside] * (1 - a))).max()
    error_inside = np.abs(marked[inside] - j60_original[inside]).max()
    page_one = pixels(document[0].render(scale=1).to_pil())
    page_one_original = pixels(pdfium.PdfDocument(str(J60_PDF))[0].render(scale=1).to_pil())
    print(f"vector pdf: outside max error {error_outside}, inside max change {error_inside}")
    assert error_outside <= 2
    assert error_inside <= 1
    # A spotlight with no location on page 1 covers that whole page.
    assert np.abs(page_one - np.round(page_one_original * (1 - a))).max() <= 2


def test_pdf_writes_one_png_per_marked_page_unless_a_page_is_chosen(tmp_path):
    locations = load_locations("j60-joint.locations.json") + [{"id": "title", "page": 1, "box": [0.4, 0.1, 0.7, 0.2]}]
    result = render(J60_PDF, locations, "box", tmp_path / "marked.png")
    assert result.outputs == [str(tmp_path / "marked-p1.png"), str(tmp_path / "marked-p2.png")]
    only = render(J60_PDF, locations, "box", tmp_path / "only.png", page=2)
    assert only.outputs == [str(tmp_path / "only.png")]


# ---- unresolvable Locations are reported, never dropped ------------------------------------------------------


def test_unresolvable_locations_are_listed_with_a_reason(tmp_path):
    good = load_locations("j60-joint.locations.json")[0]
    bad = [
        {"id": "no-such-page", "page": 9, "box": [0.1, 0.1, 0.2, 0.2]},
        {"id": "inverted-box", "page": 2, "box": [0.5, 0.5, 0.4, 0.6]},
        {"id": "html-on-pdf", "html": {"text_quote": {"exact": "Peak Torque"}}},
        {"id": "text-on-pdf", "text": {"start": 0, "end": 4}},
        {"id": "two-kinds", "page": 1, "box": [0.1, 0.1, 0.2, 0.2], "text": {"start": 0, "end": 4}},
    ]
    result = render(J60_PDF, [good, *bad], "highlight", tmp_path / "out.png")
    assert [entry.location["id"] for entry in result.unresolved] == [location["id"] for location in bad]
    reasons = {entry.location["id"]: entry.reason for entry in result.unresolved}
    assert "does not exist" in reasons["no-such-page"]
    assert "x0 < x1" in reasons["inverted-box"]
    assert "html Location cannot address" in reasons["html-on-pdf"]
    assert [mark.id for mark in result.marks] == [good["id"]]


def test_cli_prints_the_result_and_reports_unresolved(tmp_path, capsys):
    locations_file = tmp_path / "locations.json"
    locations_file.write_text(json.dumps(load_locations("j60-joint.locations.json") + [{"id": "lost", "page": 7, "box": [0, 0, 1, 1]}]))
    out = tmp_path / "cli.png"
    code = cli_main([str(J60_PDF), "--locations", str(locations_file), "--style", "spotlight", "--color", "#102030", "--out", str(out)])
    captured = capsys.readouterr()
    assert code == 0
    report = json.loads(captured.out)
    assert report["outputs"] == [str(out)]
    assert len(report["marks"]) == 4
    assert report["unresolved"][0]["location"]["id"] == "lost"
    assert "unresolved location" in captured.err
    marked = pixels(out)
    outside = ~touching_mask(marked.shape, [mark["rect"] for mark in report["marks"]])
    a = alpha(STYLE_DEFAULTS["spotlight"]["opacity"])
    expected = np.round(original_pdf_page(2) * (1 - a) + np.array([0x10, 0x20, 0x30]) * a)
    assert np.abs(marked[outside] - expected[outside]).max() <= 1


# ---- image (a band of the eRob80 page screenshot) and visual twin (a band of CubeMars') ---------------------------------------------------


def test_image_blackout_and_highlight(tmp_path):
    original = pixels(EROB_JPG)
    locations = load_locations(EROB_LOCATIONS)
    result = render(EROB_JPG, locations, "blackout", tmp_path / "erob-blackout.png")
    marked = pixels(tmp_path / "erob-blackout.png")
    assert marked.shape == original.shape
    rects = mark_rects(result)
    assert len(rects) == 3 and result.unresolved == []
    assert marked[~touching_mask(marked.shape, rects)].max() == 0
    assert np.array_equal(marked[inside_mask(marked.shape, rects)], original[inside_mask(marked.shape, rects)])

    result = render(EROB_JPG, locations, {"name": "highlight", "color": "#00ff00", "opacity": 0.5}, tmp_path / "erob-hl.png")
    marked = pixels(tmp_path / "erob-hl.png")
    inside = inside_mask(marked.shape, mark_rects(result))
    expected = np.round(original * (1 - alpha(0.5)) + np.array([0, 255, 0]) * alpha(0.5))
    assert np.abs(marked[inside] - expected[inside]).max() <= 1


def test_html_with_visual_twin_draws_boxes_on_the_screenshot(tmp_path):
    original = pixels(CUBEMARS_TWIN_JPG)
    twin_locations = load_locations(CUBEMARS_TWIN_LOCATIONS)
    html_locations = load_locations("cubemars-ak80-9.html.locations.json")
    result = render(CUBEMARS_HTML, twin_locations + html_locations[:1], "box", tmp_path / "twin-box.png", twin=CUBEMARS_TWIN_JPG)
    marked = pixels(tmp_path / "twin-box.png")
    rects = mark_rects(result)
    band = inside_mask(marked.shape, rects, grow=2) & ~touching_mask(marked.shape, rects)
    assert np.abs(marked[band] - np.array(ImageColor.getrgb(STYLE_DEFAULTS["box"]["color"]))).max() == 0
    assert np.array_equal(marked[inside_mask(marked.shape, rects)], original[inside_mask(marked.shape, rects)])
    # An html Location has no position on a screenshot: reported, not guessed.
    assert len(result.unresolved) == 1 and "visual twin" in result.unresolved[0].reason


# ---- HTML (archived CubeMars page) and text (markdown), laid out by headless Chromium ------------------------------


@pytest.mark.parametrize(
    ("source", "locations_name", "suffix"),
    [(CUBEMARS_HTML, "cubemars-ak80-9.html.locations.json", "html"), (CUBEMARS_MD, "cubemars-ak80-9-spec.md.locations.json", "md")],
)
def test_browser_rendered_sources_blackout_and_spotlight(tmp_path, source, locations_name, suffix):
    locations = load_locations(locations_name)
    baseline = render(source, [], "highlight", tmp_path / f"{suffix}-plain.png")
    assert baseline.marks == []
    original = pixels(tmp_path / f"{suffix}-plain.png")

    blackout = render(source, locations, "blackout", tmp_path / f"{suffix}-blackout.png")
    assert blackout.unresolved == []
    assert sorted({mark.id for mark in blackout.marks}) == sorted(location["id"] for location in locations)
    marked = pixels(tmp_path / f"{suffix}-blackout.png")
    rects = mark_rects(blackout)
    assert marked.shape == original.shape
    assert marked[~touching_mask(marked.shape, rects)].max() == 0
    inside = inside_mask(marked.shape, rects)
    assert np.array_equal(marked[inside], original[inside])

    spotlight = render(source, locations, "spotlight", tmp_path / f"{suffix}-spotlight.png")
    marked = pixels(tmp_path / f"{suffix}-spotlight.png")
    a = STYLE_DEFAULTS["spotlight"]["opacity"]
    outside = ~touching_mask(marked.shape, mark_rects(spotlight))
    error = np.abs(marked[outside] - original[outside] * (1 - a)).max()
    print(f"{suffix} spotlight: outside max error {error:.2f} vs original × {1 - a}")
    assert error <= 1


def test_text_to_html_page(tmp_path):
    out = tmp_path / "spec.html"
    result = render(CUBEMARS_MD, load_locations("cubemars-ak80-9-spec.md.locations.json"), "highlight", out)
    assert result.outputs == [str(out)] and result.unresolved == []
    page = out.read_text(encoding="utf-8")
    assert "Rated torque (Nm)" in page and "export function highlight" in page
    assert not list(tmp_path.glob("*.png"))


def test_source_kind_is_sniffed_without_an_extension(tmp_path):
    blob = tmp_path / "6ac4a2918c13"  # cached sources are named by hash
    blob.write_bytes(J60_PDF.read_bytes())
    result = render(blob, load_locations("j60-joint.locations.json"), "box", tmp_path / "blob.png")
    assert len(result.marks) == 4
    assert Image.open(tmp_path / "blob.png").size == (1191, 1684)
