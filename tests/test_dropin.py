"""The drop-in viewer (Zach, 2026-11-16): each highlight carries an id, an optional label and an optional href; a click
emits `highlight-click` for the host to use, and follows the href only if there is one and nobody took the click.
Plus the two accepted defaults: one tint where marks of one style overlap, and an unbroken text underline. And the
sha256 pin (CONTRACT §2) in the interactive form."""

from __future__ import annotations

import hashlib
import io

import numpy as np
import pytest
from PIL import Image

from source_highlighter import STYLE_DEFAULTS, render

from .conftest import EROB_JPG, EROB_LOCATIONS, J60_PDF, draw, load_locations, pixels

IMAGE_URL = f"/tests/fixtures/{EROB_JPG.name}"

CLICK_JS = """async ({id, prevent}) => {
    const surface = document.getElementById("surface");
    const seen = [];
    const listener = (event) => { seen.push(event.detail); if (prevent) event.preventDefault(); };
    surface.addEventListener("highlight-click", listener);
    const mark = surface.querySelector(`.sh-mark[data-id="${id}"]`);
    const r = mark.getBoundingClientRect();
    const target = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    target.click();
    surface.removeEventListener("highlight-click", listener);
    return {seen, tag: mark.tagName, href: mark.getAttribute("href"), hit: target === mark, hash: location.hash};
}"""


def test_a_click_emits_highlight_click_with_the_location(harness):
    locations = load_locations(EROB_LOCATIONS)
    locations[0] = {**locations[0], "href": "#rated-torque"}
    draw(harness, {"kind": "image", "url": IMAGE_URL}, locations, "highlight", zoom=1)

    plain = harness.evaluate(CLICK_JS, {"id": locations[1]["id"], "prevent": False})
    assert plain["hit"], "the mark is not what a click at its centre lands on"
    assert plain["tag"] == "DIV" and plain["href"] is None and plain["hash"] == ""
    detail = plain["seen"][0]
    assert detail["id"] == locations[1]["id"] and detail["label"] == locations[1]["label"] and detail["href"] is None
    assert detail["location"] == locations[1] and detail["page"] == 1 and detail["style"] == "highlight" and detail["layer"] == 0

    taken = harness.evaluate(CLICK_JS, {"id": locations[0]["id"], "prevent": True})
    assert taken["tag"] == "A" and taken["href"] == "#rated-torque"
    assert taken["seen"][0]["href"] == "#rated-torque" and taken["hash"] == "", "a listener took the click, yet the link was followed"

    followed = harness.evaluate(CLICK_JS, {"id": locations[0]["id"], "prevent": False})
    assert followed["hash"] == "#rated-torque", "an href mark nobody took was not followed"


def page_shot(harness) -> np.ndarray:
    return pixels(Image.open(io.BytesIO(harness.locator(".sh-page").first.screenshot())))


@pytest.mark.parametrize("form", ["interactive", "static"])
def test_overlapping_marks_of_one_style_show_one_tint(harness, tmp_path, form):
    """Default accepted 2026-11-16: one tint within a style, like a highlighter pen. Two boxes overlap in the middle;
    the overlap must look exactly like the parts only one box covers."""
    a = {"id": "a", "page": 1, "box": [0.10, 0.10, 0.50, 0.30]}
    b = {"id": "b", "page": 1, "box": [0.30, 0.20, 0.70, 0.40]}
    style = {"name": "highlight", "padding": 0, "color": "#ffe600", "opacity": 0.45}
    plain = np.full((400, 600, 3), 255, dtype=np.uint8)
    source = tmp_path / "white.png"
    Image.fromarray(plain).save(source)
    if form == "static":
        image = pixels(render(source, [a, b], style, tmp_path / "out.png").outputs[0])
    else:
        served = J60_PDF.parent / ".white-overlap.png"
        served.write_bytes(source.read_bytes())
        try:
            draw(harness, {"kind": "image", "url": f"/tests/fixtures/{served.name}"}, [a, b], style, zoom=1)
            image = page_shot(harness)
        finally:
            served.unlink()
    only_a, both, only_b = image[100, 100], image[100, 250], image[140, 380]  # (y, x): a only, overlap, b only
    expected = np.round(255 * (1 - 0.45) + np.array([255, 230, 0]) * 0.45)
    assert np.abs(only_a - expected).max() <= 2 and np.abs(only_b - expected).max() <= 2, (only_a, only_b, expected)
    assert np.abs(both - only_a).max() <= 1, f"overlap {both} is darker than one mark {only_a}"


def test_text_underline_is_unbroken_under_descenders(harness):
    """Default accepted 2026-11-16: "a line under each line of the target" — no gaps where g, j, p, y dip."""
    text = "gypsy jumping quietly"
    draw(harness, {"kind": "text", "text": text}, [{"id": "u", "text": {"start": 0, "end": len(text)}}], {"name": "underline", "width": 2}, width=600)
    image = page_shot(harness)
    rect = harness.evaluate("""() => { const page = document.querySelector('.sh-page').getBoundingClientRect();
        const mark = document.querySelector('.sh-mark').getBoundingClientRect();
        return [mark.left - page.left, mark.top - page.top, mark.right - page.left, mark.bottom - page.top]; }""")
    red = np.array([229, 50, 45])
    x0, y0, x1, y1 = (int(round(v)) for v in rect)
    pad = int(STYLE_DEFAULTS["underline"]["padding"])
    band = image[y0:y1 + 4, x0 + pad + 1 : x1 - pad - 1]
    painted = (np.abs(band - red) <= 60).all(axis=2)
    best_row = painted.mean(axis=1).max()
    assert best_row >= 0.99, f"the underline's best row is only {best_row:.0%} red: it breaks at the descenders"


def test_the_interactive_form_refuses_a_box_made_on_other_bytes(harness):
    pdf_sha = hashlib.sha256(J60_PDF.read_bytes()).hexdigest()
    image_sha = hashlib.sha256(EROB_JPG.read_bytes()).hexdigest()
    box = load_locations("j60-joint.locations.json")[0]
    pinned = [{**box, "id": "good", "sha256": pdf_sha}, {**box, "id": "stale", "sha256": "0" * 64}]
    drawn = draw(harness, {"kind": "pdf", "url": "/tests/fixtures/j60-joint.pdf"}, pinned, "box", zoom=1)
    assert {mark["id"] for mark in drawn["drawn"]} == {"good"}
    assert [entry["location"]["id"] for entry in drawn["result"]["unresolved"]] == ["stale"]
    assert "other bytes" in drawn["result"]["unresolved"][0]["reason"]
    image_box = load_locations(EROB_LOCATIONS)[0]
    drawn = draw(harness, {"kind": "image", "url": IMAGE_URL},
                 [{**image_box, "id": "good", "sha256": image_sha}, {**image_box, "id": "stale", "sha256": pdf_sha}], "box", zoom=1)
    assert {mark["id"] for mark in drawn["drawn"]} == {"good"}
    assert [entry["location"]["id"] for entry in drawn["result"]["unresolved"]] == ["stale"]
