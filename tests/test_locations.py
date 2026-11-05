"""The best-effort Locations (CONTRACT §2): a note heading / ^block, a page + quote, a whole page, and a quote with no
Location at all — plus focus() staying inside its own frame and archived pages staying quiet.

Expected rectangles come from outside the module: pdfplumber's character boxes for a PDF quote, pdfium page sizes,
and DOM ranges the test builds itself from offsets it computes in Python.
"""

from __future__ import annotations

import base64
import io
import urllib.parse

import pdfplumber
import pypdfium2 as pdfium
import pytest
from PIL import Image

from .conftest import FIXTURES, draw

TOLERANCE_PX = 1.0
NOTE = FIXTURES / "vault" / "Bench notes.md"
NOTE_URL = "/tests/fixtures/vault/" + urllib.parse.quote(NOTE.name)
NOTE_TEXT = NOTE.read_text(encoding="utf-8")
LRS_PDF = FIXTURES / "lrs-350.pdf"
LRS_URL = "/tests/fixtures/lrs-350.pdf"
DUPLICATES_URL = "/tests/fixtures/duplicate-values.html"
STYLE = {"name": "box", "padding": 0}


def reasons(drawn) -> dict:
    return {entry["location"]["id"]: entry["reason"] for entry in drawn["result"]["unresolved"]}


def rects_by_id(drawn) -> dict:
    out: dict[str, list] = {}
    for mark in drawn["drawn"]:
        out.setdefault(mark["id"], []).append(mark["rect"])
    return out


def assert_rects(actual: list, expected: list, label: str) -> float:
    assert len(actual) == len(expected), f"{label}: {len(actual)} rects drawn, {len(expected)} expected"
    worst = 0.0
    for drawn_rect, expected_rect in zip(actual, expected):
        off = max(abs(a - b) for a, b in zip(drawn_rect, expected_rect))
        assert off <= TOLERANCE_PX, f"{label}: drawn {drawn_rect} vs expected {expected_rect} (off by {off:.2f} px)"
        worst = max(worst, off)
    return worst


def text_range_rects(harness, start: int, end: int) -> list:
    """Line rects of [start, end) (UTF-16 offsets) in the rendered <pre>, relative to the page element."""
    return harness.evaluate(
        """([start, end]) => {
            const page = document.querySelector(".sh-page");
            const node = page.querySelector(".sh-text").firstChild;
            const range = document.createRange();
            range.setStart(node, start);
            range.setEnd(node, end);
            const origin = page.getBoundingClientRect();
            return [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0)
                .map((r) => [r.left - origin.left, r.top - origin.top, r.right - origin.left, r.bottom - origin.top]);
        }""",
        [start, end],
    )


# ---- note: heading and/or ^block ------------------------------------------------------------------------------------


def expected_note_spans() -> dict:
    """Section and block offsets computed here, by plain string search, not by the module's parser."""
    torque = NOTE_TEXT.index("## Torque\n")
    torque_end = NOTE_TEXT.index("## Open questions")
    shoulder = NOTE_TEXT.index("The eRob80I-50")
    shoulder_end = NOTE_TEXT.index(" ^shoulder-call")
    power = NOTE_TEXT.index("One Mean Well")
    power_end = NOTE_TEXT.index(" ^power-budget")
    assert NOTE_TEXT.count("## Torque") == 2  # the second sits inside a code fence
    return {
        "torque": (torque, len(NOTE_TEXT[:torque_end].rstrip())),
        "shoulder": (shoulder, shoulder_end),
        "power": (power, power_end),
    }


def test_note_heading_and_block_mark_their_section(harness):
    locations = [
        {"id": "torque", "note": {"heading": "Torque"}},
        {"id": "shoulder", "note": {"block": "shoulder-call"}},
        {"id": "shoulder-in-torque", "note": {"heading": "Torque", "block": "shoulder-call"}},
        {"id": "power", "note": {"block": "power-budget"}},
        {"id": "power-not-in-torque", "note": {"heading": "Torque", "block": "power-budget"}},
        {"id": "twice", "note": {"heading": "Open questions"}},
        {"id": "missing", "note": {"heading": "Speed"}},
    ]
    drawn = draw(harness, {"kind": "text", "url": NOTE_URL}, locations, STYLE, width=900)
    unresolved = reasons(drawn)
    assert set(unresolved) == {"power-not-in-torque", "twice", "missing"}
    assert "ambiguous" in unresolved["twice"] and "2" in unresolved["twice"]
    assert "Speed" in unresolved["missing"]
    marks = rects_by_id(drawn)
    spans = expected_note_spans()
    for location_id, span in [("torque", "torque"), ("shoulder", "shoulder"), ("shoulder-in-torque", "shoulder"), ("power", "power")]:
        assert_rects(marks[location_id], text_range_rects(harness, *spans[span]), location_id)
    assert len(marks["torque"]) > 5  # the whole section, sub-heading and fence included
    assert harness.errors == []


def test_note_location_needs_a_text_source(harness):
    drawn = draw(harness, {"kind": "html", "url": DUPLICATES_URL}, [{"id": "n", "note": {"heading": "Torque"}}], STYLE)
    assert "note Location" in reasons(drawn)["n"]


# ---- page + quote, and quote alone, on a PDF ------------------------------------------------------------------------


def pdfplumber_rect(page_number: int, quote: str) -> list:
    """The quote's character boxes from pdfplumber (pdfminer's font metrics), as one rect in PDF points."""
    with pdfplumber.open(LRS_PDF) as pdf:
        found = pdf.pages[page_number - 1].search(quote, regex=False)
    assert len(found) == 1, (quote, found)
    hit = found[0]
    return [hit["x0"], hit["top"], hit["x1"], hit["bottom"]]


@pytest.mark.parametrize("zoom", [1, 2])
def test_page_quote_on_a_pdf_lands_on_pdfplumber_character_boxes(harness, zoom):
    quotes = {"rated-48": "349.2W", "eff-36": "88.5%", "eff-24": "88%"}
    locations = [{"id": key, "page": 2, "quote": quote} for key, quote in quotes.items()]
    locations.append({"id": "anywhere", "quote": "349.2W"})
    drawn = draw(harness, {"kind": "pdf", "url": LRS_URL}, locations, STYLE, zoom=zoom)
    assert drawn["result"]["unresolved"] == []
    marks = rects_by_id(drawn)
    worst = 0.0
    for key, quote in quotes.items():
        expected = [value * zoom for value in pdfplumber_rect(2, quote)]
        worst = max(worst, assert_rects(marks[key], [expected], key))
    assert marks["anywhere"] == marks["rated-48"]
    found = {mark["id"]: mark.get("found") for mark in drawn["result"]["marks"]}
    assert found["rated-48"] == {"exact": True, "errors": 0, "text": "349.2W"}
    print(f"pdf page+quote zoom {zoom}: worst {worst:.3f} px")
    assert harness.errors == []


def test_pdf_quote_twice_is_ambiguous_and_never_picked(harness):
    locations = [
        {"id": "twice", "page": 2, "quote": "350.4W"},  # LRS-350-24 and LRS-350-48 both print it
        {"id": "twice-anywhere", "quote": "350.4W"},
        {"id": "absent", "page": 2, "quote": "1234.5W"},
        {"id": "wrong-page", "page": 1, "quote": "349.2W"},
        {"id": "no-page", "page": 9, "quote": "349.2W"},
    ]
    drawn = draw(harness, {"kind": "pdf", "url": LRS_URL}, locations, STYLE)
    unresolved = reasons(drawn)
    assert set(unresolved) == {loc["id"] for loc in locations}
    assert "ambiguous" in unresolved["twice"] and "2 times" in unresolved["twice"]
    assert "ambiguous" in unresolved["twice-anywhere"]
    assert "not" in unresolved["absent"] and "not" in unresolved["wrong-page"]
    assert "does not exist" in unresolved["no-page"]
    assert drawn["drawn"] == []


# ---- page only ------------------------------------------------------------------------------------------------------


def test_page_only_outlines_the_whole_page(harness):
    document = pdfium.PdfDocument(str(LRS_PDF))
    width, height = document[1].get_size()
    document.close()
    drawn = draw(harness, {"kind": "pdf", "url": LRS_URL}, [{"id": "p2", "page": 2}, {"id": "p9", "page": 9}], "highlight", zoom=1.5)
    assert set(reasons(drawn)) == {"p9"}
    marks = [mark for mark in drawn["drawn"] if mark["id"] == "p2"]
    assert len(marks) == 1 and marks[0]["page"] == 2
    assert_rects([marks[0]["rect"]], [[0, 0, width * 1.5, height * 1.5]], "p2")
    outline = harness.evaluate(
        """() => { const m = document.querySelector('.sh-mark[data-id="p2"]'); const s = getComputedStyle(m);
                   return [parseFloat(s.outlineWidth), parseFloat(s.outlineOffset), s.backgroundColor]; }"""
    )
    assert outline[0] > 0 and outline[1] == -outline[0]  # drawn inside the page edge, so the page's clip keeps it
    assert outline[2] in ("rgba(0, 0, 0, 0)", "transparent")  # an outline, never a page-sized fill

    buffer = io.BytesIO()
    Image.new("RGB", (320, 180), "white").save(buffer, "PNG")
    image_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    drawn = draw(harness, {"kind": "image", "url": image_url}, [{"id": "whole", "page": 1}], "box", zoom=2)
    assert drawn["result"]["unresolved"] == []
    assert_rects([drawn["drawn"][0]["rect"]], [[0, 0, 640, 360]], "image page")


def test_quote_on_an_image_is_reported(harness):
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, "PNG")
    image_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    drawn = draw(harness, {"kind": "image", "url": image_url}, [{"id": "q", "page": 1, "quote": "21 N·m"}], STYLE)
    assert "no text" in reasons(drawn)["q"]


# ---- quote alone, on HTML and text ----------------------------------------------------------------------------------


def html_cell_rect(harness, row_label: str, column: int) -> list:
    return harness.evaluate(
        """([rowLabel, column]) => {
            const frame = document.querySelector("iframe");
            const row = [...frame.contentDocument.querySelectorAll("tr")].find((tr) => tr.children[1].textContent === rowLabel);
            const range = frame.contentDocument.createRange();
            range.selectNodeContents(row.children[column - 1]);
            const r = range.getBoundingClientRect();
            return [r.left, r.top, r.right, r.bottom];
        }""",
        [row_label, column],
    )


def test_quote_only_on_html_finds_one_place_or_reports(harness):
    locations = [
        {"id": "unique", "quote": "Maximum Motor Phase Current"},
        {"id": "spaced", "quote": "Maximum  Motor\nPhase Current"},  # whitespace never counts
        {"id": "typo", "quote": "Maximum Motor Phase Curent"},  # one edit: found, but flagged approximate
        {"id": "twice", "quote": "30A"},
        {"id": "twice-fuzzy", "quote": "Absolute Encodr"},  # approximately in both model columns
        {"id": "absent", "quote": "Rated speed"},
    ]
    drawn = draw(harness, {"kind": "html", "url": DUPLICATES_URL}, locations, STYLE)
    unresolved = reasons(drawn)
    assert set(unresolved) == {"twice", "twice-fuzzy", "absent"}
    assert "ambiguous" in unresolved["twice"] and "2 times" in unresolved["twice"]
    assert "ambiguous" in unresolved["twice-fuzzy"]
    marks = rects_by_id(drawn)
    cell = html_cell_rect(harness, "Maximum Motor Phase Current", 2)
    for location_id in ("unique", "spaced", "typo"):
        assert_rects(marks[location_id], [cell], location_id)
    found = {mark["id"]: mark.get("found") for mark in drawn["result"]["marks"]}
    assert found["unique"]["exact"] is True and found["unique"]["errors"] == 0
    assert found["typo"]["exact"] is False and found["typo"]["errors"] == 1
    assert harness.errors == []


def test_quote_only_on_text(harness):
    quote = "suits the wrist"
    start = NOTE_TEXT.index(quote)
    drawn = draw(harness, {"kind": "text", "url": NOTE_URL}, [{"id": "q", "quote": quote}, {"id": "twice", "quote": "## Open questions"}], STYLE, width=900)
    assert set(reasons(drawn)) == {"twice"}
    assert_rects(rects_by_id(drawn)["q"], text_range_rects(harness, start, start + len(quote)), "text quote")


# ---- focus() and archived pages -------------------------------------------------------------------------------------


def test_focus_scrolls_its_own_container_never_the_parent_frame(harness, static_server):
    harness.evaluate(
        """async (url) => {
            document.body.style.height = "4000px";
            const frame = document.createElement("iframe");
            Object.assign(frame.style, {position: "absolute", top: "1500px", left: "0", width: "900px", height: "500px", border: "0"});
            frame.src = url;
            document.body.appendChild(frame);
            await new Promise((resolve) => frame.addEventListener("load", resolve, {once: true}));
            while (!frame.contentWindow.sourceHighlighter) await new Promise((resolve) => setTimeout(resolve, 20));
            window.scrollTo(0, 1200);
        }""",
        f"{static_server}/tests/harness.html",
    )
    inner = harness.frames[1]
    note = NOTE_TEXT * 4  # tall enough to need scrolling inside a 300 px container
    draw(inner, {"kind": "text", "text": note}, [{"id": "deep", "text": {"start": len(note) // 2, "end": len(note) // 2 + 20}}], STYLE, width=800, container_height=300)
    before = harness.evaluate("() => window.scrollY")
    assert inner.evaluate("() => window.handle.focus('deep')") is True
    after = harness.evaluate("() => window.scrollY")
    centred = inner.evaluate(
        """() => { const s = document.getElementById("surface").getBoundingClientRect();
                   const m = document.querySelector('.sh-mark[data-id="deep"]').getBoundingClientRect();
                   return [document.getElementById("surface").scrollTop, (m.top + m.bottom) / 2 - (s.top + s.bottom) / 2]; }"""
    )
    assert after == before == 1200
    assert centred[0] > 300 and abs(centred[1]) <= 2


def test_archived_page_logs_nothing_and_keeps_its_layout(harness):
    drawn = draw(harness, {"kind": "html", "url": "/tests/fixtures/cubemars-ak80-9.html"}, [], STYLE)
    assert drawn["result"]["unresolved"] == []
    harness.wait_for_timeout(300)
    counts = harness.evaluate(
        """() => { const d = document.querySelector("iframe").contentDocument;
                   return {scripts: d.querySelectorAll("script").length, images: d.querySelectorAll("img").length,
                           fetching: d.querySelectorAll("img[src]:not([src^='data:']), link[href], iframe[src]").length}; }"""
    )
    assert counts["scripts"] == 0 and counts["fetching"] == 0
    assert counts["images"] > 100  # the <img> boxes stay, so the layout matches the page as captured
    assert harness.console == [], harness.console[:5]


# ---- the static form ------------------------------------------------------------------------------------------------


def test_static_form_outlines_a_whole_pdf_page_and_reports_a_pdf_quote(tmp_path):
    from source_highlighter import render

    from .conftest import pixels

    locations = [{"id": "p2", "page": 2}, {"id": "q", "page": 2, "quote": "349.2W"}]
    result = render(LRS_PDF, locations, "highlight", tmp_path / "page.png", scale=1)
    assert [entry.location["id"] for entry in result.unresolved] == ["q"]
    assert "interactive form" in result.unresolved[0].reason
    document = pdfium.PdfDocument(str(LRS_PDF))
    width, height = document[1].get_size()
    document.close()
    assert [mark.rect.as_list() for mark in result.marks] == [[0, 0, width, height]]
    marked = pixels(tmp_path / "page.png")
    plain = pixels(render(LRS_PDF, [], "highlight", tmp_path / "plain.png", scale=1, page=2).outputs[0])
    red = marked[int(height // 2), 0:2]
    assert (red == [229, 50, 45]).all(), red  # the outline sits inside the left edge, in the box colour
    interior = (slice(10, -10), slice(10, -10))
    assert (marked[interior] == plain[interior]).all()  # and the page is not filled


def test_static_form_resolves_note_and_quote_locations_through_the_module(tmp_path):
    from source_highlighter import render

    locations = [{"id": "torque", "note": {"heading": "Torque"}}, {"id": "q", "quote": "suits the wrist"}, {"id": "typo", "quote": "suits teh wrist"}]
    result = render(NOTE, locations, "highlight", tmp_path / "note.png")
    assert result.unresolved == []
    assert {mark.id for mark in result.marks} == {"torque", "q", "typo"}
