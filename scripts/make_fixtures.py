"""Regenerate the test Locations (tests/fixtures/*.locations.json) and the markdown text fixture.

Every Location points at a real spec value and its label ("Peak Torque" → "19.94N.m"), so a mark that lands
in the wrong place is visible to the eye, not just to a test.

* J60 Joint PDF (DEEP Robotics): the PDF is a scan — no text layer, so pdfplumber finds no words. Boxes are
  hand-picked rough regions, tightened to the ink inside them.
* eRob80 page screenshot and CubeMars twin: hand-picked regions, tightened the same way.
* CubeMars archived HTML: source_range / xpath / text_quote computed from the file and from Chromium's DOM.
* CubeMars spec markdown: the HTML spec table written out as a markdown table; text Locations are code-point
  ranges into it.

Run: uv run python scripts/make_fixtures.py [--snap <folder with eRob80.html.jpg and the CubeMars .jpg>]
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
DEFAULT_SNAP = os.environ.get(
    "SOURCE_HIGHLIGHTER_SNAP",
    "/tmp/claude-1000/-home-bam-zach-brain--claude-worktrees-datasheet-ingestion-parser-e51022/"
    "af9ade88-bf96-4801-9b31-49dc0f495b6a/scratchpad/snap",
)


def tighten(gray: np.ndarray, rough: tuple[int, int, int, int], *, ink_is_light: bool, threshold: int) -> list[int]:
    """Shrink a rough pixel region to the bounding box of the ink inside it."""
    x0, y0, x1, y1 = rough
    region = gray[y0:y1, x0:x1]
    ink = region > threshold if ink_is_light else region < threshold
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    if not len(rows):
        raise ValueError(f"no ink in {rough}")
    return [x0 + int(cols[0]), y0 + int(rows[0]), x0 + int(cols[-1]) + 1, y0 + int(rows[-1]) + 1]


def normalized(pixel_box: list[int], width: int, height: int) -> list[float]:
    x0, y0, x1, y1 = pixel_box
    return [round(x0 / width, 5), round(y0 / height, 5), round(x1 / width, 5), round(y1 / height, 5)]


def page_box_locations(gray: np.ndarray, picks: list[dict], *, page: int, ink_is_light: bool, threshold: int) -> list[dict]:
    height, width = gray.shape
    locations = []
    for pick in picks:
        box = tighten(gray, pick["rough"], ink_is_light=ink_is_light, threshold=threshold)
        locations.append({"id": pick["id"], "label": pick["label"], "page": page, "box": normalized(box, width, height)})
    return locations


def j60_locations() -> list[dict]:
    # Rough regions in pixels of page 2 rendered at scale 1.5 (893 × 1263 px).
    picks = [
        {"id": "peak-torque-label", "label": "Peak Torque", "rough": (395, 384, 500, 406)},
        {"id": "peak-torque-j60-6", "label": "J60-6 peak torque", "rough": (145, 384, 250, 406)},
        {"id": "peak-torque-j60-10", "label": "J60-10 peak torque", "rough": (630, 384, 735, 406)},
        {"id": "weight-j60-6", "label": "J60-6 weight", "rough": (170, 215, 230, 236)},
    ]
    scale = 4.0
    document = pdfium.PdfDocument(str(FIXTURES / "j60-joint.pdf"))
    gray = np.asarray(document[1].render(scale=scale).to_pil().convert("L"))
    factor = scale / 1.5
    for pick in picks:
        pick["rough"] = tuple(round(value * factor) for value in pick["rough"])
    # White text on a dark table; the faint joint watermark behind it stays below 170.
    return page_box_locations(gray, picks, page=2, ink_is_light=True, threshold=170)


def erob_locations(snap: Path) -> list[dict]:
    gray = np.asarray(Image.open(snap / "eRob80.html.jpg").convert("L"))
    picks = [
        {"id": "rated-torque-label", "label": "额定扭矩 (rated torque)", "rough": (58, 3306, 140, 3342)},
        {"id": "rated-torque-erob80i-50", "label": "eRob80I-50 rated torque", "rough": (330, 3306, 405, 3342)},
        {"id": "model-erob80i-50", "label": "eRob80I-50", "rough": (318, 3226, 415, 3248)},
    ]
    return page_box_locations(gray, picks, page=1, ink_is_light=False, threshold=150)


def cubemars_twin_locations(snap: Path) -> list[dict]:
    gray = np.asarray(Image.open(snap / "ak80-9-v3-0-robotic-actuator.html.jpg").convert("L"))
    picks = [
        {"id": "rated-torque-label", "label": "Rated torque (Nm)", "rough": (200, 11183, 370, 11212)},
        {"id": "rated-torque-value", "label": "Rated torque", "rough": (392, 11183, 425, 11212)},
    ]
    return page_box_locations(gray, picks, page=1, ink_is_light=False, threshold=150)


def cubemars_html_locations(html: str) -> list[dict]:
    """html Locations for spec cells: offsets into the decoded file, plus xpath and quote from Chromium's DOM."""
    from playwright.sync_api import sync_playwright

    # (id, label, the spec row's label cell, the text to mark: that label cell or the value cell after it)
    wanted = [
        ("rated-torque-label", "Rated torque (Nm)", "Rated torque (Nm)", "Rated torque (Nm)"),
        ("rated-torque-value", "Rated torque", "Rated torque (Nm)", "9"),
        ("peak-torque-label", "Peak torque (Nm)", "Peak torque (Nm)", "Peak torque (Nm)"),
        ("peak-torque-value", "Peak torque", "Peak torque (Nm)", "22"),
    ]
    cells = []
    for location_id, label, row_label, exact in wanted:
        anchor = f"<td>{row_label}</td>" if exact == row_label else f"<td>{row_label}</td><td>{exact}</td>"
        assert html.count(anchor) == 1, anchor
        # Python str offsets count code points, which is what source_range counts (CONTRACT §2).
        cell_start = html.index(anchor) + len(anchor) - len(exact) - len("</td>")
        assert html[cell_start : cell_start + len(exact)] == exact
        cells.append((location_id, label, cell_start, exact))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        # The page's own scripts never run; evaluate() still works through Playwright's utility world.
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content(html, wait_until="domcontentloaded")
        dom_facts = page.evaluate(
            """(exacts) => {
                const xpathOf = (node) => {
                    const steps = [];
                    for (; node && node.nodeType === 1; node = node.parentElement) {
                        const tag = node.localName;
                        if (tag === "html" || tag === "body") { steps.unshift(tag); continue; }
                        let index = 1;
                        for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling)
                            if (sibling.localName === tag) index += 1;
                        steps.unshift(`${tag}[${index}]`);
                    }
                    return "/" + steps.join("/");
                };
                // No filter callback: with page scripts off, a TreeWalker refuses to call one.
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let text = ""; const starts = [];
                for (let n = walker.nextNode(); n; n = walker.nextNode()) {
                    if (n.parentElement.closest("script, style, noscript, template")) continue;
                    starts.push([n, text.length]); text += n.data;
                }
                const cells = [...document.querySelectorAll("td")];
                return exacts.map(([anchorLabel, exact]) => {
                    const labelCell = cells.find((td) => td.textContent === anchorLabel);
                    const cell = exact === anchorLabel ? labelCell : labelCell.nextElementSibling;
                    const textNode = cell.firstChild;
                    const at = starts.find(([n]) => n === textNode)[1];
                    const squeeze = (s) => s.replace(/\\s+/g, " ");
                    return {
                        xpath: xpathOf(cell),
                        prefix: squeeze(text.slice(Math.max(0, at - 80), at)).slice(-32).trimStart(),
                        suffix: squeeze(text.slice(at + exact.length, at + exact.length + 80)).slice(0, 32).trimEnd(),
                    };
                });
            }""",
            [[row_label, exact] for _, _, row_label, exact in wanted],
        )
        browser.close()

    locations = []
    for (location_id, label, cell_start, exact), facts in zip(cells, dom_facts):
        locations.append(
            {
                "id": location_id,
                "label": label,
                "html": {
                    "source_range": {"start": cell_start, "end": cell_start + len(exact)},
                    "xpath": facts["xpath"],
                    "text_quote": {"exact": exact, "prefix": facts["prefix"], "suffix": facts["suffix"]},
                },
            }
        )
    return locations


def cubemars_markdown(html: str) -> tuple[str, list[dict]]:
    """The CubeMars spec table as markdown, plus text Locations (code points) for two label/value pairs."""
    table = re.search(r'<table class="spaced-table">(.*?)</table>', html, re.DOTALL).group(1)
    rows = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", table, re.DOTALL):
        cells = [
            html_lib.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", " ", cell))).strip()
            for cell in re.findall(r"<td>(.*?)</td>", row_html, re.DOTALL)
        ]
        cells = [re.sub(r"\s+", " ", cell) for cell in cells]
        if any(cells):
            rows.append(cells)
    lines = [
        "# CubeMars AK80-9 V3.0 KV100 — Specification",
        "",
        "Source: https://www.cubemars.com/product/ak80-9-v3-0-robotic-actuator.html (archived page, table rows as published)",
        "",
        "| Parameter | Value | Parameter | Value |",
        "|---|---|---|---|",
    ]
    lines += ["| " + " | ".join(cells) + " |" for cells in rows]
    markdown = "\n".join(lines) + "\n"
    locations = []
    for location_id, label, row_label, value, mark_value in [
        ("rated-torque-label", "Rated torque (Nm)", "Rated torque (Nm)", "9", False),
        ("rated-torque-value", "Rated torque", "Rated torque (Nm)", "9", True),
        ("peak-torque-label", "Peak torque (Nm)", "Peak torque (Nm)", "22", False),
        ("peak-torque-value", "Peak torque", "Peak torque (Nm)", "22", True),
    ]:
        needle = f"| {row_label} | {value} |"
        assert markdown.count(needle) == 1, needle
        label_start = markdown.index(needle) + len("| ")
        exact = value if mark_value else row_label
        start = label_start + len(row_label) + len(" | ") if mark_value else label_start
        assert markdown[start : start + len(exact)] == exact, (markdown[start : start + len(exact)], exact)
        locations.append({"id": location_id, "label": label, "text": {"start": start, "end": start + len(exact)}})
    return markdown, locations


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", path.relative_to(REPO))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snap", default=DEFAULT_SNAP)
    snap = Path(parser.parse_args().snap)

    write_json(FIXTURES / "j60-joint.locations.json", j60_locations())
    html = (FIXTURES / "cubemars-ak80-9.html").read_bytes().decode("utf-8")
    write_json(FIXTURES / "cubemars-ak80-9.html.locations.json", cubemars_html_locations(html))
    markdown, text_locations = cubemars_markdown(html)
    (FIXTURES / "cubemars-ak80-9-spec.md").write_text(markdown, encoding="utf-8")
    write_json(FIXTURES / "cubemars-ak80-9-spec.md.locations.json", text_locations)
    if (snap / "eRob80.html.jpg").exists():
        write_json(FIXTURES / "erob80.locations.json", erob_locations(snap))
        write_json(FIXTURES / "cubemars-ak80-9.twin.locations.json", cubemars_twin_locations(snap))
    else:
        print(f"snap folder {snap} missing: kept the existing eRob80 / CubeMars-twin locations")


if __name__ == "__main__":
    main()
