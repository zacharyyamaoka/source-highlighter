"""html Locations resolve exactly, never to the first place their text occurs (CONTRACT §2).

The fixture is the J60 Joint parameter table, where both models print the same values (30A, CAN, 1Mbps) in
one row — the datasheet case that "first occurrence" gets wrong half the time. Every Location targets the
second (J60-10) cell, and the drawn mark is measured against that cell, computed by the test itself.
"""

from __future__ import annotations

import pytest

from source_highlighter import render

from .conftest import CUBEMARS_HTML, FIXTURES, draw, load_locations

PAGE = FIXTURES / "duplicate-values.html"
URL = "/tests/fixtures/duplicate-values.html"
HTML = PAGE.read_text(encoding="utf-8")
STYLE = {"name": "box", "padding": 0}
ROW = "Maximum Motor Phase Current"


def cell_source_range(value: str, row_label: str, column: int) -> dict:
    """Code points of `value` in the row's first (column 1) or last (column 3) cell — computed from the file."""
    row = f"<td>{value}</td><td>{row_label}</td><td>{value}</td>"
    assert HTML.count(row) == 1
    at = HTML.index(row) + len("<td>")
    if column == 3:
        at += len(f"{value}</td><td>{row_label}</td><td>")
    assert HTML[at : at + len(value)] == value
    return {"start": at, "end": at + len(value)}


def cell_rect(harness, row_label: str, column: int) -> list[float]:
    """The cell's text rectangle, measured independently of the module (relative to the page element)."""
    return harness.evaluate(
        """([rowLabel, column]) => {
            const frame = document.querySelector("iframe");
            const row = [...frame.contentDocument.querySelectorAll("tr")].find((tr) => tr.children[1].textContent === rowLabel);
            const range = frame.contentDocument.createRange();
            range.selectNodeContents(row.children[column - 1]);
            const rect = range.getBoundingClientRect();
            return [rect.left, rect.top, rect.right, rect.bottom];
        }""",
        [row_label, column],
    )


def drawn_by_id(drawn) -> dict:
    return {mark["id"]: mark["rect"] for mark in drawn["drawn"]}


def assert_same_rect(actual, expected):
    assert actual == pytest.approx(expected, abs=1.0), (actual, expected)


def test_each_resolution_path_lands_on_the_second_of_two_identical_cells(harness):
    second = cell_source_range("30A", ROW, 3)
    locations = [
        {"id": "source-range-only", "html": {"source_range": second}},
        {"id": "quote-with-context", "html": {"text_quote": {"exact": "30A", "prefix": ROW, "suffix": "CAN"}}},
        {"id": "xpath-cell", "html": {"xpath": "/html/body/table/tbody/tr[4]/td[3]", "text_quote": {"exact": "30A"}}},
        {"id": "xpath-raw-markup", "html": {"xpath": "/html/body/table/tr[4]/td[3]", "text_quote": {"exact": "30A"}}},
        # the row holds "30A" twice: the quote alone is ambiguous inside it, the source_range decides
        {"id": "xpath-row-and-range", "html": {"xpath": "/html/body/table/tbody/tr[4]", "text_quote": {"exact": "30A"}, "source_range": second}},
        # a stale xpath (matches nothing) falls through to the exact source_range
        {"id": "stale-xpath-and-range", "html": {"xpath": "/html/body/div[3]/table/tr[9]/td[1]", "source_range": second}},
    ]
    drawn = draw(harness, {"kind": "html", "url": URL}, locations, STYLE)
    assert drawn["result"]["unresolved"] == []
    marks = drawn_by_id(drawn)
    first_cell, second_cell = cell_rect(harness, ROW, 1), cell_rect(harness, ROW, 3)
    assert abs(first_cell[0] - second_cell[0]) > 100  # the two cells are far apart, so a wrong pick cannot pass
    for location in locations:
        assert_same_rect(marks[location["id"]], second_cell)


def test_context_picks_the_first_cell_when_that_is_what_it_describes(harness):
    location = {"id": "first", "html": {"text_quote": {"exact": "30A", "prefix": "30.50N.m", "suffix": ROW}}}
    drawn = draw(harness, {"kind": "html", "url": URL}, [location], STYLE)
    assert_same_rect(drawn_by_id(drawn)["first"], cell_rect(harness, ROW, 1))


def test_a_quote_that_matches_twice_is_reported_ambiguous_never_picked(harness):
    locations = [
        {"id": "bare-quote", "html": {"text_quote": {"exact": "30A"}}},
        {"id": "row-without-range", "html": {"xpath": "/html/body/table/tbody/tr[4]", "text_quote": {"exact": "30A"}}},
        {"id": "context-too-short", "html": {"text_quote": {"exact": "CAN", "prefix": "Method"}}},  # only the 2nd fits
    ]
    drawn = draw(harness, {"kind": "html", "url": URL}, locations, STYLE)
    unresolved = {entry["location"]["id"]: entry["reason"] for entry in drawn["result"]["unresolved"]}
    assert set(unresolved) == {"bare-quote", "row-without-range"}
    assert "ambiguous" in unresolved["bare-quote"] and "2 times" in unresolved["bare-quote"]
    assert "ambiguous" in unresolved["row-without-range"]
    assert_same_rect(drawn_by_id(drawn)["context-too-short"], cell_rect(harness, "Communication Method", 3))


def test_source_range_outside_page_text_is_reported(harness):
    def span_of(text, occurrence=1):
        at = -1
        for _ in range(occurrence):
            at = HTML.index(text, at + 1)
        return {"start": at, "end": at + len(text)}

    locations = [
        {"id": "in-title", "html": {"source_range": span_of("J60 Joint parameters")}},
        {"id": "in-attribute", "html": {"source_range": span_of('lang="en"')}},
        {"id": "in-comment", "html": {"source_range": span_of("J60 Joint datasheet")}},
        {"id": "in-style", "html": {"source_range": span_of("border-collapse")}},
        {"id": "past-the-end", "html": {"source_range": {"start": len(HTML) + 5, "end": len(HTML) + 9}}},
        {"id": "with-entity", "html": {"source_range": span_of("Encoder Type &amp; Resolution")}},
        {"id": "ends-mid-entity", "html": {"source_range": {"start": span_of("Encoder Type &amp;")["start"], "end": span_of("Encoder Type &am")["end"]}}},
    ]
    drawn = draw(harness, {"kind": "html", "url": URL}, locations, STYLE)
    unresolved = {entry["location"]["id"]: entry["reason"] for entry in drawn["result"]["unresolved"]}
    assert set(unresolved) == {"in-title", "in-attribute", "in-comment", "in-style", "past-the-end"}
    for location_id in ("in-title", "in-attribute", "in-comment", "in-style"):
        assert "does not point into the page's text" in unresolved[location_id]
    marks = drawn_by_id(drawn)
    assert_same_rect(marks["with-entity"], cell_rect(harness, "Encoder Type & Resolution", 2))
    # "&am" widens to the whole "&amp;", so the mark covers "Encoder Type &"
    assert marks["ends-mid-entity"][0] == pytest.approx(marks["with-entity"][0], abs=1.0)
    assert marks["ends-mid-entity"][2] < marks["with-entity"][2]


def test_static_form_resolves_the_same_second_cell(harness, tmp_path):
    location = {"id": "second", "html": {"source_range": cell_source_range("1Mbps", "Communications Baud Rate", 3)}}
    result = render(PAGE, [location], STYLE, tmp_path / "out.png")
    assert result.unresolved == []
    draw(harness, {"kind": "html", "url": URL}, [location], STYLE, width=1280)
    assert_same_rect(result.marks[0].rect.as_list(), cell_rect(harness, "Communications Baud Rate", 3))


def test_real_page_source_range_alone_matches_the_full_location(harness):
    """On the archived CubeMars page, source_range by itself lands where xpath + quote do ("9" occurs often)."""
    full = load_locations("cubemars-ak80-9.html.locations.json")
    range_only = [{"id": f"{location['id']}/range", "html": {"source_range": location["html"]["source_range"]}} for location in full]
    drawn = draw(harness, {"kind": "html", "url": "/tests/fixtures/cubemars-ak80-9.html"}, full + range_only, STYLE)
    assert drawn["result"]["unresolved"] == []
    assert CUBEMARS_HTML.read_text(encoding="utf-8").count(">9<") > 1
    marks = drawn_by_id(drawn)
    for location in full:
        assert_same_rect(marks[f"{location['id']}/range"], marks[location["id"]])


def test_source_range_wins_over_a_stale_xpath_that_holds_the_same_quote(harness, tmp_path):
    """CONTRACT §2: source_range resolves first. A stale xpath that points at the OTHER "30A" cell (where the quote is
    unique) must not win — both in the interactive form and in the static form."""
    second = cell_source_range("30A", ROW, 3)
    location = {"id": "stale", "html": {"xpath": "/html/body/table/tbody/tr[4]/td[1]", "text_quote": {"exact": "30A"}, "source_range": second}}
    drawn = draw(harness, {"kind": "html", "url": URL}, [location], STYLE, width=1280)
    assert drawn["result"]["unresolved"] == []
    second_cell, first_cell = cell_rect(harness, ROW, 3), cell_rect(harness, ROW, 1)
    assert abs(first_cell[0] - second_cell[0]) > 100
    assert_same_rect(drawn_by_id(drawn)["stale"], second_cell)
    result = render(PAGE, [location], STYLE, tmp_path / "out.png")
    assert result.unresolved == []
    assert_same_rect(result.marks[0].rect.as_list(), second_cell)
