"""Rendered markdown (Zach, 2026-11-16: "Lets support raw and rendered; for the renderer use our like_obsidian_md viewer").

`{kind: "markdown"}` draws a note through the vendored like-obsidian viewer; `{kind: "text"}` stays the raw text.
A Location still counts into the markdown source; on the rendered note it anchors on the same text.

The oracle is the rendered DOM's own table, read here and not by the module: a word the source puts in table row r,
column c (parsed from the GFM below, independently of source_msgs) must be marked inside that row and column's
<td>/<th>. That is where a repeated value ("24 V" in two model columns) would land wrong.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from PIL import Image

from source_highlighter import render

from .conftest import CUBEMARS_MD, FIXTURES, REPO, load_locations, pixels

VIEWER = REPO / "js" / "like-obsidian"
ROUNDTRIP = FIXTURES / "roundtrip"


def test_the_vendored_viewer_is_the_recorded_build():
    record = json.loads((VIEWER / "VENDOR.json").read_text(encoding="utf-8"))
    manifest_bytes = (VIEWER / "manifest.json").read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == record["manifest_sha256"]
    files = json.loads(manifest_bytes)["files"]
    assert len(files) == record["files"] and record["commit"]
    on_disk = {path.name for path in VIEWER.iterdir()} - {"VENDOR.json", "manifest.json", "LICENSE"}
    assert on_disk == set(files)
    for name, sha256 in files.items():
        assert hashlib.sha256((VIEWER / name).read_bytes()).hexdigest() == sha256, name


def table_cells(markdown: str) -> dict[tuple[int, int], tuple[int, int, int]]:
    """(start, end) code points of each GFM table cell's trimmed text → (table, row, col), rows counted from the
    header row as 0, the separator line skipped; 0-based columns."""
    cells = {}
    table, row, offset, previous_was_table = -1, 0, 0, False
    for line in markdown.split("\n"):
        is_table = line.lstrip().startswith("|")
        if is_table and not previous_was_table:
            table, row = table + 1, 0
        if is_table and not set(line.strip()) <= set("|-: "):
            bars = [index for index, char in enumerate(line) if char == "|"]
            for col, (left, right) in enumerate(zip(bars, bars[1:])):
                raw = line[left + 1 : right]
                start = left + 1 + len(raw) - len(raw.lstrip())
                cells[(offset + start, offset + start + len(raw.strip()))] = (table, row, col)
            row += 1
        previous_was_table = is_table
        offset += len(line) + 1
    return cells


DRAW_RENDERED_JS = """async ({markdown, locations}) => {
    const surface = document.getElementById("surface");
    surface.replaceChildren();
    const handle = window.sourceHighlighter.highlight(surface, {source: {kind: "markdown", text: markdown}, locations, style: "box", zoom: 1, width: 900});
    window.handle = handle;
    const result = await handle.ready;
    const frame = surface.querySelector("iframe");
    const tables = [...frame.contentDocument.querySelectorAll("table")].map((table) => [...table.rows].map((row) =>
        [...row.cells].map((cell) => { const r = cell.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })));
    const origin = surface.querySelector(".sh-page").getBoundingClientRect();
    const drawn = [...surface.querySelectorAll(".sh-mark")].map((mark) => {
        const r = mark.getBoundingClientRect();
        return {id: mark.dataset.id, rect: [r.left - origin.left, r.top - origin.top, r.right - origin.left, r.bottom - origin.top]};
    });
    return {result, tables, drawn, hasCodeMirror: Boolean(frame.contentDocument.querySelector(".cm-content"))};
}"""


@pytest.mark.parametrize("case", ["04-multi-model", "08-two-column", "11-mixed-chinese", "12-full-datasheet"])
def test_every_word_of_a_note_is_marked_in_its_rendered_place(harness, case):
    markdown = (ROUNDTRIP / case / "pdf" / "document.md").read_text(encoding="utf-8")
    words = json.loads((ROUNDTRIP / case / "pdf" / "index.json").read_text(encoding="utf-8"))["words"]
    locations = [{"id": f"w{i}", "text": word["range"]} for i, word in enumerate(words)]
    out = harness.evaluate(DRAW_RENDERED_JS, {"markdown": markdown, "locations": locations})
    assert out["hasCodeMirror"], "the note was not drawn by the like-obsidian viewer"
    assert out["result"]["unresolved"] == [], out["result"]["unresolved"][:3]
    found = {}
    for mark in out["result"]["marks"]:
        found.setdefault(mark["id"], mark)
    cells = table_cells(markdown)
    rects: dict[str, list] = {}
    for mark in out["drawn"]:
        rects.setdefault(mark["id"], []).append(mark["rect"])
    in_tables = 0
    for i, word in enumerate(words):
        text = markdown[word["range"]["start"] : word["range"]["end"]]
        assert "".join(found[f"w{i}"]["found"]["text"].split()) == "".join(text.split()), (case, text, found[f"w{i}"]["found"])
        cell = next(((t, r, c) for (s, e), (t, r, c) in cells.items() if s <= word["range"]["start"] and word["range"]["end"] <= e), None)
        if cell is None:
            continue
        table, row, col = cell
        x0, y0, x1, y1 = out["tables"][table][row][col]
        for rect in rects[f"w{i}"]:
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2  # the mark is padded, so its centre is the test
            assert x0 <= cx <= x1 and y0 <= cy <= y1, f"{case}: {text!r} (table {table} row {row} col {col}) marked at {rect}, cell is {[x0, y0, x1, y1]}"
        in_tables += 1
    assert in_tables >= 10, f"{case}: only {in_tables} words were in tables"


def test_raw_stays_the_default_and_rendered_is_asked_for(tmp_path):
    locations = load_locations("cubemars-ak80-9-spec.md.locations.json")
    raw = render(CUBEMARS_MD, locations, "highlight", tmp_path / "raw.png")
    rendered = render(CUBEMARS_MD, locations, "highlight", tmp_path / "rendered.png", markdown="rendered")
    assert raw.unresolved == [] and rendered.unresolved == []
    assert {mark.id for mark in raw.marks} == {mark.id for mark in rendered.marks} == {loc["id"] for loc in locations}
    raw_rects = {mark.id: mark.rect.as_list() for mark in raw.marks}
    rendered_rects = {mark.id: mark.rect.as_list() for mark in rendered.marks}
    assert raw_rects != rendered_rects  # a different layout: a monospace <pre> vs Obsidian's table
    # The rendered table keeps "Rated torque (Nm)" and its value "9" on one row, the value to the right.
    label, value = rendered_rects["rated-torque-label"], rendered_rects["rated-torque-value"]
    assert abs((label[1] + label[3]) / 2 - (value[1] + value[3]) / 2) < 2 and value[0] > label[2]
    # The highlight is painted under the value: yellow-tinted paper at the mark's middle row.
    image = pixels(Image.open(tmp_path / "rendered.png"))
    x0, y0, x1, y1 = (round(v) for v in rendered_rects["peak-torque-label"])
    row = image[(y0 + y1) // 2, x0 + 2 : x1 - 2]
    assert ((row[:, 2] < row[:, 0] - 60) & (row[:, 0] > 200)).mean() > 0.3, "no yellow under the rendered mark"


def test_rendered_markdown_refuses_what_it_cannot_place(harness):
    markdown = "# Notes\n\n[a link](https://example.com/24V)\n\n24V here and 24V there.\n"
    locations = [
        {"id": "url", "text": {"start": markdown.index("example"), "end": markdown.index("example") + 7}},  # a link target: not shown
        {"id": "twice", "quote": "24V"},
        {"id": "second", "text": {"start": markdown.rindex("24V"), "end": markdown.rindex("24V") + 3}},  # 3 in source, 2 shown
        {"id": "page", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]},
    ]
    out = harness.evaluate(DRAW_RENDERED_JS, {"markdown": markdown, "locations": locations})
    reasons = {entry["location"]["id"]: entry["reason"] for entry in out["result"]["unresolved"]}
    assert set(reasons) == {"url", "twice", "second", "page"}, reasons
    assert "ambiguous" in reasons["second"] and "ambiguous" in reasons["twice"]
    assert out["drawn"] == []
