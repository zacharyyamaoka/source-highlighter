"""The sha256 pin (R2, CONTRACT §2): a geometry selector carries the sha256 of the file it was measured on — the
`Source`'s own `sha256` — and is refused, never painted, on other bytes."""

from __future__ import annotations

import pytest

from source_highlighter import Page, PageBox, Source, TextQuote, from_location, parse, render

from .conftest import EROB_JPG, EROB_LOCATIONS, J60_PDF, load_locations


def test_a_box_pinned_to_its_source_is_drawn_and_on_another_file_is_refused(tmp_path):
    pdf = Source.from_file(J60_PDF)
    image = Source.from_file(EROB_JPG)
    x0, y0, x1, y1 = load_locations("j60-joint.locations.json")[0]["box"]
    box = PageBox(2, x0, y0, x1 - x0, y1 - y0, id="peak", sha256=pdf.sha256)
    result = pdf.highlight(box, "box").save(tmp_path / "pdf.png")
    assert [mark.id for mark in result.marks] == ["peak"] and result.unresolved == []
    wrong = image.highlight([PageBox(1, 0.1, 0.1, 0.1, 0.1, id="stale", sha256=pdf.sha256), Page(1, id="page", sha256=image.sha256)], "box")
    result = wrong.save(tmp_path / "image.png")
    assert [entry.location["id"] for entry in result.unresolved] == ["stale"]
    assert "other bytes" in result.unresolved[0].reason
    assert [mark.id for mark in result.marks] == ["page"]


def test_an_unpinned_box_still_draws_on_any_bytes(tmp_path):
    result = render(EROB_JPG, load_locations(EROB_LOCATIONS), "box", tmp_path / "out.png")
    assert result.unresolved == [] and len(result.marks) == 3


def test_the_pin_round_trips_through_compact_strings_and_dicts():
    sha = "ab" * 32
    box = PageBox(2, 0.1, 0.2, 0.3, 0.05, id="x", sha256=sha)
    assert box.compact().endswith(f"&sha256={sha}")
    assert parse(box.compact()) == box
    location = box.to_location("pdf")
    assert location["sha256"] == sha and from_location(location) == box
    assert parse(Page(3, sha256=sha).compact()) == Page(3, sha256=sha)
    with pytest.raises(ValueError, match="geometry selector"):
        parse(f"text=Rated&sha256={sha}")
    with pytest.raises(ValueError, match="64 lowercase hex"):
        PageBox(1, 0.1, 0.1, 0.1, 0.1, sha256="not-a-hash")
    assert "sha256" not in TextQuote("Rated").to_location("html")
