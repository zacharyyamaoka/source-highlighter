"""The interactive form in headless Chromium: drawn marks measured with getBoundingClientRect.

Expected rectangles come from outside the module: PDF page sizes from pdfium (not PDF.js), image sizes from
Pillow, and DOM ranges built by the test itself from the Location's xpath / UTF-16 offsets.
"""

from __future__ import annotations

import pypdfium2 as pdfium
import pytest
from PIL import Image

from source_highlighter import STYLE_DEFAULTS, STYLES

from .conftest import EROB_JPG, J60_PDF, draw, load_locations, requires

TOLERANCE_PX = 1.0


def expected_page_box(location, page_width, page_height, padding, zoom):
    x0, y0, x1, y1 = location["box"]
    rect = [x0 * page_width - padding, y0 * page_height - padding, x1 * page_width + padding, y1 * page_height + padding]
    rect = [max(0, rect[0]), max(0, rect[1]), min(page_width, rect[2]), min(page_height, rect[3])]
    return [value * zoom for value in rect]


def assert_rects_close(drawn, expected, label):
    worst = max(abs(a - b) for a, b in zip(drawn, expected))
    assert worst <= TOLERANCE_PX, f"{label}: drawn {drawn} vs expected {expected} (off by {worst:.2f} px)"
    return worst


@pytest.mark.parametrize("zoom", [1, 2])
@pytest.mark.parametrize("style", STYLES)
def test_pdf_marks_land_on_their_boxes(harness, static_server, zoom, style):
    locations = load_locations("j60-joint.locations.json")
    document = pdfium.PdfDocument(str(J60_PDF))
    page_width, page_height = document[1].get_size()  # pdfium's view of the page, not PDF.js's
    document.close()
    drawn = draw(harness, {"kind": "pdf", "url": "/tests/fixtures/j60-joint.pdf"}, locations, style, zoom=zoom)
    assert drawn["result"]["unresolved"] == []
    assert drawn["zoom"] == zoom
    assert drawn["pageSizes"][1] == pytest.approx([page_width * zoom, page_height * zoom], abs=TOLERANCE_PX)
    marks = {mark["id"]: mark for mark in drawn["drawn"]}
    assert sorted(marks) == sorted(location["id"] for location in locations)
    worst = 0.0
    for location in locations:
        mark = marks[location["id"]]
        assert mark["page"] == 2
        expected = expected_page_box(location, page_width, page_height, STYLE_DEFAULTS[style]["padding"], zoom)
        worst = max(worst, assert_rects_close(mark["rect"], expected, location["id"]))
    print(f"pdf {style} zoom {zoom}: worst {worst:.3f} px")
    assert harness.errors == []


def test_spotlight_mask_holes_match_the_marks(harness):
    locations = load_locations("j60-joint.locations.json")
    draw(harness, {"kind": "pdf", "url": "/tests/fixtures/j60-joint.pdf"}, locations, "spotlight", zoom=1.5)
    holes = harness.evaluate(
        """() => [...document.querySelectorAll('.sh-page[data-page="2"] mask rect[fill="black"]')]
              .map((r) => ['x', 'y', 'width', 'height'].map((k) => Number(r.getAttribute(k))))"""
    )
    marks = harness.evaluate(
        """() => [...document.querySelectorAll('.sh-page[data-page="2"] .sh-mark')].map((m) =>
              [m.offsetLeft, m.offsetTop, m.offsetWidth, m.offsetHeight].map(Number))"""
    )
    assert len(holes) == len(marks) == 4
    for hole, mark in zip(holes, marks):
        assert hole == pytest.approx(mark, abs=TOLERANCE_PX)
    # Page 1 has no location: its veil covers the whole page with no hole.
    page_one = harness.evaluate("""() => document.querySelectorAll('.sh-page[data-page="1"] mask rect').length""")
    assert page_one == 1


@requires(EROB_JPG)
@pytest.mark.parametrize("zoom", [0.5, 1.5])
def test_image_marks_land_on_their_boxes(harness, static_server, zoom):
    # The screenshot lives outside the repo; the static server only serves the checkout, so copy it in.
    served = J60_PDF.parent.parent / ".erob80-served.jpg"
    served.write_bytes(EROB_JPG.read_bytes())
    try:
        locations = load_locations("erob80.locations.json")
        width, height = Image.open(EROB_JPG).size
        drawn = draw(harness, {"kind": "image", "url": "/tests/.erob80-served.jpg"}, locations, "box", zoom=zoom)
    finally:
        served.unlink()
    marks = {mark["id"]: mark for mark in drawn["drawn"]}
    for location in locations:
        expected = expected_page_box(location, width, height, STYLE_DEFAULTS["box"]["padding"], zoom)
        assert_rects_close(marks[location["id"]]["rect"], expected, location["id"])


def test_focus_scrolls_the_mark_into_the_middle_of_the_view(harness):
    locations = load_locations("j60-joint.locations.json")
    draw(harness, {"kind": "pdf", "url": "/tests/fixtures/j60-joint.pdf"}, locations, "box", zoom=2, container_height=500)
    for location_id in ("peak-torque-j60-10", "weight-j60-6"):
        geometry = harness.evaluate(
            """async (id) => {
                await window.handle.focus(id);
                const view = document.getElementById("surface").getBoundingClientRect();
                const mark = document.querySelector(`.sh-mark[data-id="${id}"]`).getBoundingClientRect();
                return {view: [view.left, view.top, view.right, view.bottom], mark: [mark.left, mark.top, mark.right, mark.bottom],
                        scrollTop: document.getElementById("surface").scrollTop};
            }""",
            location_id,
        )
        view, mark = geometry["view"], geometry["mark"]
        assert geometry["scrollTop"] > 0
        assert view[0] <= mark[0] and mark[2] <= view[2] and view[1] <= mark[1] and mark[3] <= view[3]
        assert abs((mark[1] + mark[3]) / 2 - (view[1] + view[3]) / 2) <= 2, geometry
    # focus with a zoom re-renders at that zoom, then scrolls
    zoom = harness.evaluate("async () => { await window.handle.focus('peak-torque-label', {zoom: 3}); return window.handle.zoom; }")
    assert zoom == 3


def test_html_marks_match_the_cells_and_use_the_highlight_api(harness):
    locations = load_locations("cubemars-ak80-9.html.locations.json")
    drawn = draw(harness, {"kind": "html", "url": "/tests/fixtures/cubemars-ak80-9.html"}, locations, "highlight", zoom=1)
    assert drawn["result"]["unresolved"] == []
    padding = STYLE_DEFAULTS["highlight"]["padding"]
    independent = harness.evaluate(
        """(locations) => {
            const frameDocument = document.querySelector("iframe").contentDocument;
            return locations.map((location) => {
                const cell = frameDocument.evaluate(location.html.xpath, frameDocument, null, 9, null).singleNodeValue;
                const range = frameDocument.createRange();
                range.selectNodeContents(cell);
                const rect = range.getBoundingClientRect();
                return [rect.left, rect.top, rect.right, rect.bottom];
            });
        }""",
        locations,
    )
    marks = {mark["id"]: mark for mark in drawn["drawn"]}
    for location, cell_rect in zip(locations, independent):
        expected = [cell_rect[0] - padding, cell_rect[1] - padding, cell_rect[2] + padding, cell_rect[3] + padding]
        assert_rects_close(marks[location["id"]]["rect"], expected, location["id"])
    painted = harness.evaluate(
        """() => {
            const frameWindow = document.querySelector("iframe").contentWindow;
            return [...frameWindow.CSS.highlights.values()].flatMap((h) => [...h].map((range) => range.toString()));
        }"""
    )
    assert sorted(painted) == sorted(location["html"]["text_quote"]["exact"] for location in locations)
    sandbox = harness.evaluate("() => document.querySelector('iframe').getAttribute('sandbox')")
    assert sandbox == "allow-same-origin"  # never allow-scripts


def test_html_location_falls_back_from_a_stale_xpath_to_the_text_quote(harness):
    location = load_locations("cubemars-ak80-9.html.locations.json")[1]  # the "9" next to Rated torque
    stale = {**location, "html": {**location["html"], "xpath": "/html/body/div[99]/table/tr[1]/td[1]"}}
    quote_only = {"id": "quote-only", "html": {"text_quote": location["html"]["text_quote"]}}
    range_only = {"id": "range-only", "html": {"source_range": location["html"]["source_range"]}}
    missing = {"id": "missing", "html": {"text_quote": {"exact": "Flux capacitor"}}}
    drawn = draw(harness, {"kind": "html", "url": "/tests/fixtures/cubemars-ak80-9.html"}, [location, stale, quote_only, missing, range_only], "box")
    marks = {mark["id"]: mark["rect"] for mark in drawn["drawn"]}
    assert marks[stale["id"]] == marks[location["id"]] == marks["quote-only"]
    # source_range alone: "9" appears many times, so without the quote's context the first "9" on the page wins —
    # it resolves, but not necessarily to the same cell (a documented v0 limit).
    assert "range-only" in marks
    assert [entry["location"]["id"] for entry in drawn["result"]["unresolved"]] == ["missing"]


def test_text_ranges_count_code_points_not_utf16_units(harness):
    text = "🙂🙂 Rated torque (Nm) | 9 |\n"
    location = {"id": "label", "text": {"start": 3, "end": 20}}  # code points; UTF-16 would be 5–22
    drawn = draw(harness, {"kind": "text", "text": text}, [location], "highlight", width=600)
    painted = harness.evaluate("() => [...CSS.highlights.values()].flatMap((h) => [...h].map((r) => r.toString()))")
    assert painted == ["Rated torque (Nm)"]
    expected = harness.evaluate(
        """() => {
            const node = document.querySelector(".sh-text").firstChild;
            const range = document.createRange();
            range.setStart(node, 5); range.setEnd(node, 22);
            const rect = range.getBoundingClientRect();
            const origin = document.querySelector(".sh-page").getBoundingClientRect();
            return [rect.left - origin.left, rect.top - origin.top, rect.right - origin.left, rect.bottom - origin.top];
        }"""
    )
    padding = STYLE_DEFAULTS["highlight"]["padding"]
    assert_rects_close(drawn["drawn"][0]["rect"], [expected[0] - padding, expected[1] - padding, expected[2] + padding, expected[3] + padding], "label")


def test_update_restyles_without_reloading_and_reports_unresolved(harness):
    locations = load_locations("j60-joint.locations.json")
    draw(harness, {"kind": "pdf", "url": "/tests/fixtures/j60-joint.pdf"}, locations, "highlight", zoom=1)
    result = harness.evaluate(
        """async (locations) => {
            const canvas = document.querySelector("canvas");
            const result = await window.handle.update([...locations, {id: "gone", page: 5, box: [0, 0, 0.5, 0.5]}], "blackout");
            return {sameCanvas: canvas === document.querySelector("canvas"), result,
                    styles: [...new Set([...document.querySelectorAll(".sh-mark")].map((m) => m.className))]};
        }""",
        locations,
    )
    assert result["sameCanvas"]
    assert result["styles"] == ["sh-mark sh-blackout"]
    assert [entry["location"]["id"] for entry in result["result"]["unresolved"]] == ["gone"]
    assert "does not exist" in result["result"]["unresolved"][0]["reason"]
