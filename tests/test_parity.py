"""Both forms draw the same rectangles for the same input (CONTRACT §4): Python's burned-in marks vs the
JS overlay's getBoundingClientRect, matched by Location id, at the same scale."""

from __future__ import annotations

import pytest

from source_highlighter import STYLE_DEFAULTS, STYLES, render

from .conftest import CUBEMARS_HTML, CUBEMARS_MD, EROB_JPG, EROB_LOCATIONS, J60_PDF, draw, load_locations

TOLERANCE_PX = 1.0


def compare(python_result, js_drawn, label):
    """Every Location's rectangles in both forms, within 1 px; returns the worst difference."""
    python_rects = sorted((mark.id, mark.page, tuple(mark.rect.as_list())) for mark in python_result.marks)
    js_rects = sorted((mark["id"], mark["page"], tuple(mark["rect"])) for mark in js_drawn["drawn"])
    assert [(i, p) for i, p, _ in python_rects] == [(i, p) for i, p, _ in js_rects], label
    worst = 0.0
    for (location_id, _, ours), (_, _, theirs) in zip(python_rects, js_rects):
        difference = max(abs(a - b) for a, b in zip(ours, theirs))
        assert difference <= TOLERANCE_PX, f"{label} {location_id}: python {ours} vs js {theirs}"
        worst = max(worst, difference)
    print(f"parity {label}: {len(python_rects)} rects, worst {worst:.3f} px")
    return worst


def test_style_defaults_are_the_same_table(harness):
    js_defaults = harness.evaluate("() => window.sourceHighlighter.STYLE_DEFAULTS")
    assert js_defaults == STYLE_DEFAULTS
    assert harness.evaluate("() => window.sourceHighlighter.STYLES") == list(STYLES)


@pytest.mark.parametrize("style", STYLES)
def test_pdf_both_forms_draw_the_same_rectangles(harness, tmp_path, style):
    locations = load_locations("j60-joint.locations.json")
    python_result = render(J60_PDF, locations, style, tmp_path / "out.png", scale=2)
    js_drawn = draw(harness, {"kind": "pdf", "url": "/tests/fixtures/j60-joint.pdf"}, locations, style, zoom=2)
    compare(python_result, js_drawn, f"pdf/{style}")


def test_image_both_forms_draw_the_same_rectangles(harness, tmp_path):
    locations = load_locations(EROB_LOCATIONS)
    python_result = render(EROB_JPG, locations, "spotlight", tmp_path / "out.png")
    js_drawn = draw(harness, {"kind": "image", "url": f"/tests/fixtures/{EROB_JPG.name}"}, locations, "spotlight", zoom=1)
    compare(python_result, js_drawn, "image/spotlight")


@pytest.mark.parametrize(
    ("kind", "source", "url", "locations_name", "width"),
    [
        ("html", CUBEMARS_HTML, "/tests/fixtures/cubemars-ak80-9.html", "cubemars-ak80-9.html.locations.json", 1280),
        ("text", CUBEMARS_MD, "/tests/fixtures/cubemars-ak80-9-spec.md", "cubemars-ak80-9-spec.md.locations.json", 900),
    ],
)
def test_browser_sources_both_forms_draw_the_same_rectangles(harness, tmp_path, kind, source, url, locations_name, width):
    """The static PNG lays the page out in its own Chromium (no server, network blocked); the overlay loads it
    over HTTP into a page of a different width. The rectangles must still agree."""
    locations = load_locations(locations_name)
    python_result = render(source, locations, "box", tmp_path / "out.png")
    js_drawn = draw(harness, {"kind": kind, "url": url}, locations, "box", zoom=1, width=width)
    compare(python_result, js_drawn, f"{kind}/box")
