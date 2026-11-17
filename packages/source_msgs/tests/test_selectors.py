"""Locations: a small union of selectors. Each converts to a CONTRACT §2 dict where it can, has a compact string,
and parses back. The oracles are the fixtures' own Location files (made by scripts/make_fixtures.py, not by this
code)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from source_msgs import (
    Css,
    Note,
    Page,
    PageBox,
    TableCell,
    TextPosition,
    TextQuote,
    Unconvertible,
    XPath,
    from_location,
    parse,
)
from source_msgs.selectors import gfm_table_cell

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
MARKDOWN = (FIXTURES / "cubemars-ak80-9-spec.md").read_text(encoding="utf-8")
MARKDOWN_LOCATIONS = {loc["id"]: loc for loc in json.loads((FIXTURES / "cubemars-ak80-9-spec.md.locations.json").read_text())}
PDF_LOCATIONS = {loc["id"]: loc for loc in json.loads((FIXTURES / "j60-joint.locations.json").read_text())}

EVERY_SELECTOR = [
    TextQuote("20 Nm", prefix="Rated torque ", suffix=" Peak"),
    TextQuote("Mixed paper 23.4%", page=78),
    TextQuote("a, b & c-d"),
    TextPosition(840, 846),
    PageBox(2, 0.61, 0.40, 0.07, 0.02),
    PageBox(1, 0.0, 0.0, 1.0, 0.5),
    Page(33),
    XPath("/html/body/main/table[2]/tr[3]/td[2]"),
    Css("table.spec td:nth-child(2)"),
    TableCell(3, 2, table=2),
    Note(heading="Pricing", block="ab12cd"),
    Note(heading="Specs & limits"),
]


@pytest.mark.parametrize("selector", EVERY_SELECTOR, ids=repr)
def test_compact_round_trips(selector):
    text = selector.compact()
    assert "\n" not in text
    assert parse(text) == selector


@pytest.mark.parametrize(
    ("selector", "compact"),
    [
        (TextQuote("20 Nm", prefix="Rated torque ", suffix=" Peak"), "text=Rated%20torque%20-,20%20Nm,-%20Peak"),
        (TextPosition(840, 846), "char=840,846"),
        (PageBox(2, 0.61, 0.40, 0.07, 0.02), "page=2&xywh=percent:61,40,7,2"),
        (Page(33), "page=33"),
        (TableCell(3, 2), "table=1&row=3&col=2"),
    ],
)
def test_compact_strings_borrow_the_standards(selector, compact):
    """Text Fragments, RFC 5147 char=, Media Fragments xywh=percent:, PDF open parameters page=."""
    assert selector.compact() == compact


def test_ids_and_labels_ride_along():
    selector = PageBox(2, 0.61, 0.40, 0.07, 0.02, id="peak", label="Peak torque")
    assert parse(selector.compact()) == selector
    assert selector.to_location("pdf") == {"page": 2, "box": [0.61, 0.40, 0.68, 0.42], "id": "peak", "label": "Peak torque"}


def test_page_box_matches_the_fixture_box():
    fixture = PDF_LOCATIONS["peak-torque-j60-6"]
    x0, y0, x1, y1 = fixture["box"]
    selector = PageBox(fixture["page"], x0, y0, x1 - x0, y1 - y0)
    location = selector.to_location("pdf")
    assert location["page"] == fixture["page"]
    assert location["box"] == pytest.approx(fixture["box"], abs=1e-9)
    assert selector.to_location("image")["box"] == pytest.approx(fixture["box"], abs=1e-9)
    assert from_location(fixture) == PageBox(fixture["page"], x0, y0, x1 - x0, y1 - y0, id="peak-torque-j60-6", label="J60-6 peak torque")


@pytest.mark.parametrize("bad", [dict(page=0, x=0, y=0, w=0.1, h=0.1), dict(page=1, x=0.95, y=0, w=0.1, h=0.1),
                                 dict(page=1, x=0.1, y=0.1, w=0, h=0.1)])
def test_page_box_refuses_boxes_off_the_page(bad):
    with pytest.raises(ValueError):
        PageBox(**bad)


def test_table_cell_on_markdown_is_the_fixture_span():
    """Row 1 is the header row; the rated-torque row is the 18th body row, so row 19."""
    value = TableCell(19, 2).to_location("text", MARKDOWN)
    assert value == {"text": MARKDOWN_LOCATIONS["rated-torque-value"]["text"]}
    label = TableCell(19, 1, id="rt").to_location("text", MARKDOWN)
    assert label == {"text": MARKDOWN_LOCATIONS["rated-torque-label"]["text"], "id": "rt"}
    peak = TableCell(2, 4).to_location("text", MARKDOWN)
    assert peak == {"text": MARKDOWN_LOCATIONS["peak-torque-value"]["text"]}


def test_gfm_table_cell_edges():
    text = "intro\n\n| A | B |\n| :-- | --: |\n| x | `y` |\n\n| only |\n|---|\n| z |\n"
    start, end = gfm_table_cell(text, 1, 2, 2)
    assert text[start:end] == "`y`"
    start, end = gfm_table_cell(text, 2, 2, 1)
    assert text[start:end] == "z"
    with pytest.raises(Unconvertible, match="row 9"):
        gfm_table_cell(text, 1, 9, 1)
    with pytest.raises(Unconvertible, match="table 3"):
        gfm_table_cell(text, 3, 1, 1)


def test_table_cell_on_html_is_an_xpath():
    location = TableCell(18, 2).to_location("html")
    xpath = location["html"]["xpath"]
    assert xpath == "(//table)[1]/descendant::tr[18]/*[self::td or self::th][2]"


def test_table_cell_on_a_pdf_says_what_it_needs():
    with pytest.raises(Unconvertible, match="index.json"):
        TableCell(3, 2).to_location("pdf")


def test_text_quote_with_context_pins_a_span_in_text():
    exact = MARKDOWN_LOCATIONS["rated-torque-value"]["text"]
    location = TextQuote("9", prefix="| Rated torque (Nm) | ", suffix=" |").to_location("text", MARKDOWN)
    assert location == {"text": exact}


def test_text_quote_that_fits_twice_is_reported_not_picked():
    with pytest.raises(Unconvertible, match="ambiguous"):
        TextQuote("/", prefix="| ", suffix=" |").to_location("text", MARKDOWN)
    with pytest.raises(Unconvertible, match="not in"):
        TextQuote("nowhere to be found", prefix="x").to_location("text", MARKDOWN)


def test_text_quote_without_context_stays_a_quote_so_the_fuzzy_find_runs():
    assert TextQuote("Rated torque (Nm)").to_location("text", MARKDOWN) == {"quote": "Rated torque (Nm)"}
    assert TextQuote("Peak Torque", page=2).to_location("pdf") == {"page": 2, "quote": "Peak Torque"}
    assert TextQuote("20 Nm", prefix="Rated torque ", suffix=" Peak").to_location("html") == {
        "html": {"text_quote": {"exact": "20 Nm", "prefix": "Rated torque ", "suffix": " Peak"}}}


def test_conversions_per_kind():
    assert TextPosition(840, 846).to_location("text") == {"text": {"start": 840, "end": 846}}
    assert TextPosition(40211, 40217).to_location("html") == {"html": {"source_range": {"start": 40211, "end": 40217}}}
    assert Page(33).to_location("pdf") == {"page": 33}
    assert XPath("/html/body/p[2]").to_location("html") == {"html": {"xpath": "/html/body/p[2]"}}
    assert Css("td.value").to_location("html") == {"html": {"css": "td.value"}}
    assert Note(heading="Pricing").to_location("text") == {"note": {"heading": "Pricing"}}


@pytest.mark.parametrize(
    ("selector", "kind", "reason"),
    [
        (TextQuote("x"), "image", "no text"),
        (TextPosition(1, 2), "pdf", "text"),
        (PageBox(1, 0, 0, 1, 1), "text", "page"),
        (Page(1), "html", "page"),
        (XPath("/html"), "pdf", "HTML"),
        (Css("td"), "text", "HTML"),
        (Note(heading="x"), "html", "markdown"),
    ],
)
def test_what_cannot_convert_says_why(selector, kind, reason):
    with pytest.raises(Unconvertible, match=reason):
        selector.to_location(kind, "")


def test_from_location_reads_every_single_selector_dict():
    for location, expected in [
        ({"text": {"start": 1, "end": 2}}, TextPosition(1, 2)),
        ({"page": 78, "quote": "Mixed"}, TextQuote("Mixed", page=78)),
        ({"quote": "Rated"}, TextQuote("Rated")),
        ({"page": 3}, Page(3)),
        ({"note": {"block": "ab"}}, Note(block="ab")),
        ({"html": {"xpath": "/html"}}, XPath("/html")),
        ({"html": {"css": "td"}}, Css("td")),
        ({"html": {"source_range": {"start": 1, "end": 4}}}, TextPosition(1, 4)),
        ({"html": {"text_quote": {"exact": "9", "prefix": "a"}}}, TextQuote("9", prefix="a")),
    ]:
        assert from_location(location) == expected


def test_from_location_refuses_a_chain_it_cannot_hold_in_one_selector():
    with pytest.raises(ValueError, match="several"):
        from_location({"html": {"xpath": "/html", "source_range": {"start": 1, "end": 2}}})


def test_parse_refuses_nonsense():
    for text in ["", "colour=red", "char=5", "page=two", "text=a,b,c,d"]:
        with pytest.raises(ValueError):
            parse(text)
