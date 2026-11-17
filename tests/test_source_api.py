"""The Source API (PEP 0001) draws exactly what the CONTRACT §2 dicts it stands for draw.

Oracle: the fixtures' own Location files (scripts/make_fixtures.py), rendered through the long-standing `render()`.
A selector is right when its marks land on the same rectangles, to the pixel.
"""

from __future__ import annotations

import sys
import types
import webbrowser
from pathlib import Path

import pytest
from PIL import Image

import source_highlighter
from source_highlighter import (
    Css,
    Highlighted,
    PageBox,
    Source,
    TableCell,
    TextQuote,
    XPath,
    render,
)
from tests.conftest import CUBEMARS_HTML, CUBEMARS_MD, J60_PDF, load_locations, pixels

J60 = {loc["id"]: loc for loc in load_locations("j60-joint.locations.json")}
CUBEMARS = {loc["id"]: loc for loc in load_locations("cubemars-ak80-9.html.locations.json")}
CUBEMARS_SPEC = {loc["id"]: loc for loc in load_locations("cubemars-ak80-9-spec.md.locations.json")}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_MSGS_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SOURCE_HIGHLIGHTER_SHOW_DIR", str(tmp_path / "shown"))

    def never(*args, **kwargs):
        raise AssertionError("a test must never open a browser window")

    monkeypatch.setattr(webbrowser, "open", never)


def rects(result) -> list[list[float]]:
    return [[round(value, 3) for value in mark.rect.as_list()] for mark in result.marks]


def page_box(location: dict, **extras) -> PageBox:
    x0, y0, x1, y1 = location["box"]
    return PageBox(location["page"], x0, y0, x1 - x0, y1 - y0, **extras)


def test_source_and_selectors_are_reexported():
    import source_msgs

    assert source_highlighter.Source is source_msgs.Source
    assert source_highlighter.PageBox is source_msgs.PageBox


def test_pdf_page_box_draws_where_the_fixture_box_draws(tmp_path):
    fixture = J60["peak-torque-j60-6"]
    result = Source.from_file(J60_PDF).highlight(page_box(fixture, id="a"), "box").save(tmp_path / "new.png")
    oracle = render(J60_PDF, [{**fixture, "id": "a"}], "box", tmp_path / "old.png")
    assert rects(result) == rects(oracle)
    assert not result.unresolved
    assert (pixels(tmp_path / "new.png") == pixels(tmp_path / "old.png")).all()


def test_compact_string_draws_the_same_as_the_selector(tmp_path):
    selector = page_box(J60["weight-j60-6"])
    source = Source.from_file(J60_PDF)
    by_selector = source.highlight(selector).save(tmp_path / "a.png")
    by_string = source.highlight(selector.compact()).save(tmp_path / "b.png")
    assert rects(by_selector) == rects(by_string)


def test_render_accepts_a_source(tmp_path):
    fixture = J60["peak-torque-label"]
    from_source = render(Source.from_file(J60_PDF), [fixture], "highlight", tmp_path / "a.png")
    from_path = render(J60_PDF, [fixture], "highlight", tmp_path / "b.png")
    assert rects(from_source) == rects(from_path)


def test_html_table_cell_and_css_land_on_the_fixture_cell(tmp_path):
    source = Source.from_file(CUBEMARS_HTML)
    oracle = render(CUBEMARS_HTML, [{"id": "c", "html": {"xpath": CUBEMARS["rated-torque-value"]["html"]["xpath"]}}],
                    "box", tmp_path / "oracle.png")
    assert len(oracle.marks) == 1 and not oracle.unresolved
    cell = source.highlight(TableCell(18, 2, id="c"), "box").save(tmp_path / "cell.png")
    css = source.highlight(Css("table.spaced-table tr:nth-child(18) > td:nth-child(2)", id="c"), "box").save(tmp_path / "css.png")
    xpath = source.highlight(XPath(CUBEMARS["rated-torque-value"]["html"]["xpath"], id="c"), "box").save(tmp_path / "x.png")
    for result in (cell, css, xpath):
        assert not result.unresolved, result.unresolved
        assert rects(result) == rects(oracle)


def test_a_css_selector_matching_several_elements_is_reported_not_picked(tmp_path):
    result = Source.from_file(CUBEMARS_HTML).highlight(Css("td"), "box").save(tmp_path / "out.png")
    assert not result.marks
    assert "matched" in result.unresolved[0].reason


def test_markdown_table_cell_and_pinned_quote_land_on_the_fixture_span(tmp_path):
    source = Source.from_file(CUBEMARS_MD)
    oracle = render(CUBEMARS_MD, [{**CUBEMARS_SPEC["rated-torque-value"], "id": "v"}], "highlight", tmp_path / "o.png")
    cell = source.highlight(TableCell(19, 2, id="v")).save(tmp_path / "cell.png")
    quote = source.highlight(TextQuote("9", prefix="| Rated torque (Nm) | ", suffix=" |", id="v")).save(tmp_path / "q.png")
    assert rects(cell) == rects(oracle) == rects(quote)


def test_what_cannot_address_the_source_is_reported_with_why(tmp_path):
    result = Source.from_file(J60_PDF).highlight([TableCell(3, 2), page_box(J60["peak-torque-label"])], "box").save(
        tmp_path / "out.png"
    )
    assert len(result.marks) == 1
    [entry] = result.unresolved
    assert (entry.layer, entry.index, entry.location) == (0, 0, "table=1&row=3&col=2")
    assert "index.json" in entry.reason


def test_an_html_source_with_a_snapshot_draws_page_boxes_on_it(tmp_path):
    snapshot = tmp_path / "snapshot.jpg"
    Image.new("RGB", (400, 1000), "white").save(snapshot)
    plain = Source.from_file(CUBEMARS_HTML)
    captured = Source(origin="https://maker.example/p.html", kind="html", media_type="text/html", sha256=plain.sha256,
                      path=plain.path, stored={"snapshot": str(snapshot)})
    result = captured.highlight(PageBox(1, 0.25, 0.1, 0.5, 0.2), "highlight", padding=0).save(tmp_path / "out.png")
    assert rects(result) == [[100.0, 100.0, 300.0, 300.0]]
    assert Image.open(tmp_path / "out.png").size == (400, 1000)


def test_show_headless_writes_the_page_and_opens_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_HIGHLIGHTER_BACKEND", raising=False)
    shown = Source.from_file(J60_PDF).highlight(page_box(J60["peak-torque-j60-6"]), "spotlight").show()
    assert shown.suffix == ".html" and shown.is_file()
    assert shown.is_relative_to(tmp_path / "shown")
    page = shown.read_text(encoding="utf-8")
    assert "<img" in page and "j60-joint" in page
    images = list(shown.parent.glob("*.png"))
    assert images and Image.open(images[0]).size[0] > 100


def test_show_in_the_browser_never_raises_the_window(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, new=0, autoraise=True: opened.append((url, new, autoraise)) or True)
    shown = Source.from_file(J60_PDF).highlight(page_box(J60["peak-torque-j60-6"])).show(backend="browser")
    assert opened == [(shown.as_uri(), 2, False)]


def test_show_in_a_notebook_displays_inline(monkeypatch):
    displayed = []
    display_module = types.ModuleType("IPython.display")
    display_module.display = lambda value: displayed.append(value)
    display_module.Image = lambda data, format: ("Image", format, data[:8])
    monkeypatch.setitem(sys.modules, "IPython", types.ModuleType("IPython"))
    monkeypatch.setitem(sys.modules, "IPython.display", display_module)
    Source.from_file(J60_PDF).highlight(page_box(J60["peak-torque-j60-6"])).show(backend="notebook")
    assert displayed == [("Image", "png", b"\x89PNG\r\n\x1a\n")]


def test_repr_png_is_a_png():
    highlighted = Source.from_file(J60_PDF).highlight(page_box(J60["peak-torque-j60-6"]))
    assert isinstance(highlighted, Highlighted)
    assert highlighted._repr_png_().startswith(b"\x89PNG")


def test_unknown_backend_is_refused():
    with pytest.raises(ValueError, match="backend"):
        Source.from_file(J60_PDF).highlight(page_box(J60["peak-torque-j60-6"])).show(backend="tk")


def test_the_one_liner_takes_a_path(tmp_path):
    result = source_highlighter.highlight(str(J60_PDF), page_box(J60["peak-torque-j60-6"]), "box").save(tmp_path / "o.png")
    assert len(result.marks) == 1
    assert Path(result.outputs[0]).is_file()
