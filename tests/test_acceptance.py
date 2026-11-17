"""Acceptance: what good looks like. If these are green, any Location that names a place gets that place
visibly marked, in every style, on every kind of source, and nothing else gets marked; a Location that can't be
placed is refused, never guessed.

The oracle is NOT this module. It is markdownify's round-trip harness, Zach's "FK on markdown" (2026-10-29):
ground-truth markdown printed by Chromium to a PDF (with a text layer), a 2x page image (no text layer) and a web
page, with every markdown word's true place in each, measured by Chromium and pdfplumber and self-checked to
0.05 pt. tests/fixtures/roundtrip/<case>/ holds three of those bundles (04, 11, 12), copied unchanged from
/home/bam/markdownify/harness/out (render.py @ 8071ccf). The text arm's oracle is a DOM Range this test builds.

Splits: test_paint_* checks painting (a given place → pixels); test_anchor_* checks anchoring (a text selector
→ exactly those characters). Resolving "this value" → a Location belongs to markdownify / the extractor and is
scored by markdownify's own harness, not here.

Fuzz more: SH_FUZZ_SEEDS=1,2,3,...  (each seed draws new random spans).
"""

from __future__ import annotations

import io
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageColor

from source_highlighter import STYLE_DEFAULTS, STYLES, render

from .conftest import FIXTURES, draw, inside_mask, pixels, touching_mask

ROUNDTRIP = FIXTURES / "roundtrip"
SEEDS = [int(seed) for seed in os.environ.get("SH_FUZZ_SEEDS", "7,11").split(",")]
PDF_SCALE = 2.0
ANCHOR_TOLERANCE_PX = 1.5  # anchoring: drawn rect vs true rect, per edge, at zoom 1


# ---- the oracle: true word places from the round-trip harness ------------------------------------------------------


@dataclass(frozen=True)
class Word:
    index: int
    text: str
    start: int  # code points into document.md
    end: int
    grounding: dict  # pdf/png: {"page", "box"} (0-1 of the page); html: {"html": {...}}


@dataclass(frozen=True)
class Arm:
    case: str
    arm: str
    source: Path
    markdown: str
    words: list[Word]
    page_size: tuple[float, float]  # PDF points or image pixels; (0, 0) for html


def load_arm(case: str, arm: str) -> Arm:
    folder = ROUNDTRIP / case
    if not folder.exists():
        pytest.fail(f"round-trip truth missing: copy it from /home/bam/markdownify/harness/out/{case} (see module docstring)")
    index = json.loads((folder / arm / "index.json").read_text(encoding="utf-8"))
    markdown = (folder / "pdf" / "document.md").read_text(encoding="utf-8")
    words = [
        Word(i, markdown[w["range"]["start"] : w["range"]["end"]], w["range"]["start"], w["range"]["end"], w["grounding"])
        for i, w in enumerate(index["words"])
    ]
    page = index["pages"][0] if index["pages"] else {"width": 0, "height": 0}
    source = next((folder / arm).glob("source.*"))
    return Arm(case, arm, source, markdown, words, (page["width"], page["height"]))


def pick_spans(arm: Arm, seed: int, count: int = 5, max_words: int = 4) -> list[list[Word]]:
    """`count` random, non-overlapping runs of consecutive words (a run may cross a line, a cell or a column)."""
    rng = random.Random(f"{arm.case}/{seed}")
    taken: set[int] = set()
    spans = []
    for _ in range(count * 20):
        if len(spans) == count:
            break
        first = rng.randrange(len(arm.words))
        run = arm.words[first : first + rng.randint(1, max_words)]
        if taken & {w.index - 1 for w in run} | taken & {w.index + 1 for w in run} | taken & {w.index for w in run}:
            continue  # keep a one-word gap so "nothing else painted" is measurable between spans
        taken |= {w.index for w in run}
        spans.append(run)
    return spans


def line_hulls(run: list[Word], width: float, height: float) -> list[list[float]]:
    """The true rectangles of a run, in units of width/height: one hull per printed line, as a reader sees it."""
    lines: list[list[list[float]]] = []
    for word in run:
        x0, y0, x1, y1 = word.grounding["box"]
        rect = [x0 * width, y0 * height, x1 * width, y1 * height]
        for line in lines:
            ly0, ly1 = line[0][1], line[0][3]
            if abs((rect[1] + rect[3]) / 2 - (ly0 + ly1) / 2) < (ly1 - ly0) / 2 and rect[0] >= line[-1][0]:
                line.append(rect)
                break
        else:
            lines.append([rect])
    return [[min(r[0] for r in line), min(r[1] for r in line), max(r[2] for r in line), max(r[3] for r in line)] for line in lines]


def normalized(rect: list[float], width: float, height: float) -> list[float]:
    return [rect[0] / width, rect[1] / height, rect[2] / width, rect[3] / height]


# ---- the oracle: "is this place visibly marked, and nothing else?" -------------------------------------------------


def grow(rect, by):
    return [rect[0] - by, rect[1] - by, rect[2] + by, rect[3] + by]


def alpha(opacity: float) -> float:
    return round(opacity * 255) / 255


def assert_visibly_marked(style: str, baseline: np.ndarray, marked: np.ndarray, targets: list, *, units: float, label: str):
    """Measure one rendered output against the true target rectangles (pixels). The paint rules are CONTRACT §3 +
    README "Geometry" (the spec); WHERE comes only from the oracle. `units` = output pixels per source unit."""
    assert marked.shape == baseline.shape, f"{label}: output size changed"
    params = STYLE_DEFAULTS[style]
    pad, stroke = params["padding"] * units, params["width"] * units
    colour = np.array(ImageColor.getrgb(params["color"]))
    padded = [grow(rect, pad) for rect in targets]
    core = inside_mask(marked.shape, targets, grow=-1)  # the target itself, 1 px in from its edge
    # WHY paper only, within 6 levels: Chromium's glyph antialiasing flickers between two renders of the same
    # line; paint is unambiguous on paper, and a mark in the wrong place always lands on paper.
    ink = baseline.min(axis=2) <= 235
    near_ink = ink.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
        near_ink |= np.roll(ink, (dy, dx), axis=(0, 1))
    paper = ~near_ink
    unchanged = (np.abs(marked - baseline) <= 6).all(axis=2)

    if style == "highlight":
        expected = np.round(baseline * (1 - alpha(params["opacity"])) + colour * alpha(params["opacity"]))
        tinted = (np.abs(marked - expected) <= 6).all(axis=2)
        coverage = tinted[core & paper].mean()
        allowed = padded
        assert coverage >= 0.98, f"{label}: only {coverage:.1%} of the target's paper carries the highlight"
        for x0, y0, x1, y1 in targets:  # both ends too: a mark one character short hides inside a 98% average
            for edge in ([x0 + 1, y0 + 1, x0 + 4, y1 - 1], [x1 - 4, y0 + 1, x1 - 1, y1 - 1]):
                band = inside_mask(marked.shape, [edge]) & paper
                assert not band.any() or tinted[band].mean() >= 0.9, f"{label}: the highlight stops short of {[x0, y0, x1, y1]}"
    elif style == "box":
        allowed = [grow(rect, stroke) for rect in padded]
        ring = inside_mask(marked.shape, allowed) & ~touching_mask(marked.shape, padded)
        painted = (np.abs(marked - colour) <= 6).all(axis=2)
        assert ring.sum() > 0 and painted[ring].mean() >= 0.95, f"{label}: the box outline is missing ({painted[ring].mean():.1%} drawn)"
        assert unchanged[core & paper].mean() == 1.0, f"{label}: the box covers the target's own pixels"
    elif style == "underline":
        # Outcome, not placement: under each target line, one stroke-coloured row spans it end to end. (PDF and image
        # draw a bar below the padded box; text and HTML use the browser's text-decoration at the baseline.)
        allowed = [[x0 - pad, y0 - pad, x1 + pad, y1 + pad + stroke] for x0, y0, x1, y1 in targets]
        painted = (np.abs(marked - colour) <= 40).all(axis=2)
        # A browser underline skips descenders and a closing "|" (text-decoration-skip-ink), so a column also counts
        # as underlined where the source's own ink sits within 3 px of the underline row.
        ink_near = ink.copy()
        for dx in (-3, -2, -1, 1, 2, 3):
            ink_near |= np.roll(ink, dx, axis=1)
        for x0, y0, x1, y1 in targets:
            band = (slice(int((y0 + y1) / 2), int(np.ceil(y1 + pad + stroke)) + 1), slice(int(np.ceil(x0)), int(x1)))
            red, covered = painted[band], (painted | ink_near)[band]
            assert red.size, f"{label}: empty line {[x0, y0, x1, y1]}"
            row = int(red.mean(axis=1).argmax())  # the underline's own row
            assert red[row].mean() >= 0.6 and covered[row].mean() >= 0.97, f"{label}: no underline spans {[x0, y0, x1, y1]}"
            assert covered[row, 1:4].all() and covered[row, -4:-1].all(), f"{label}: the underline stops short of {[x0, y0, x1, y1]}"
    else:  # spotlight, blackout: the target stays as it was, the rest goes dark
        allowed = padded
        assert unchanged[core & paper].mean() == 1.0, f"{label}: {style} changed the target itself"
    outside = ~touching_mask(marked.shape, allowed, grow=1.5)
    if style == "spotlight":
        dimmed = np.round(baseline * (1 - alpha(params["opacity"])))
        ok = (np.abs(marked - dimmed) <= 6).all(axis=2)
    elif style == "blackout":
        ok = (marked <= 6).all(axis=2)
    else:
        ok = unchanged
    stray = outside & paper & ~ok
    assert stray.sum() == 0, f"{label}: {int(stray.sum())} px painted where nothing was asked, e.g. at {np.argwhere(stray)[0][::-1].tolist()}"


# ---- 1. paint: a place given as a box is visibly marked, every style, PDF + image + scanned PDF ------------------------


def scanned_pdf(arm: Arm, tmp_path: Path) -> Path:
    """The page image wrapped as a PDF with no text layer (what a scanner makes); boxes stay 0-1 of the page."""
    out = tmp_path / f"{arm.case}-scan.pdf"
    Image.open(arm.source).convert("RGB").save(out, "PDF", resolution=192)  # 1632 px over 8.5 in → the same 612x792 pt page
    return out


def baseline_of(source: Path, kind: str) -> tuple[np.ndarray, float]:
    """The unmarked page, drawn by a renderer this module does not control (pdfium / Pillow), and px per unit."""
    if kind == "png":
        return pixels(source), 1.0
    document = pdfium.PdfDocument(str(source))
    image = document[0].render(scale=PDF_SCALE, draw_annots=False).to_pil()
    document.close()
    return pixels(image), PDF_SCALE


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("kind", ["pdf", "png", "scan"])
@pytest.mark.parametrize("case", ["04-multi-model", "11-mixed-chinese"])
def test_paint_box_locations_are_visibly_marked_and_nothing_else(tmp_path, case, kind, style, seed):
    arm = load_arm(case, "png" if kind in ("png", "scan") else "pdf")
    source = scanned_pdf(arm, tmp_path) if kind == "scan" else arm.source
    width, height = arm.page_size
    spans = pick_spans(arm, seed)
    locations, targets = [], []
    for number, run in enumerate(spans):
        for line, rect in enumerate(line_hulls(run, width, height)):
            locations.append({"id": f"s{number}.{line}", "page": 1, "box": normalized(rect, width, height)})
    baseline, units = baseline_of(source, "png" if kind == "png" else "pdf")
    # true rects in OUTPUT pixels, straight from the oracle's 0-1 boxes
    out_h, out_w = baseline.shape[:2]
    targets = [[b[0] * out_w, b[1] * out_h, b[2] * out_w, b[3] * out_h] for b in (loc["box"] for loc in locations)]
    out = tmp_path / "marked.png"
    result = render(source, locations, style, out, scale=None if kind == "png" else PDF_SCALE)
    assert result.unresolved == []
    words = " | ".join(" ".join(w.text for w in run) for run in spans)
    assert_visibly_marked(style, baseline, pixels(result.outputs[0]), targets, units=units, label=f"{case}/{kind}/{style} [{words}]")


# ---- 2. anchor: a quote on a born-digital PDF lands on its true glyphs, CJK included; a repeated one is refused ------


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("case", ["04-multi-model", "11-mixed-chinese", "12-full-datasheet"])
def test_anchor_pdf_page_quote_lands_on_the_true_glyphs(harness, case, seed):
    arm = load_arm(case, "pdf")
    width, height = arm.page_size
    text = " ".join(w.text for w in arm.words)
    unique, repeated = [], []
    for run in pick_spans(arm, seed, count=8):
        quote = " ".join(w.text for w in run)
        (unique if text.count(quote) == 1 else repeated).append((quote, run))
    locations = [{"id": f"u{i}", "page": 1, "quote": q} for i, (q, _) in enumerate(unique)]
    locations += [{"id": f"r{i}", "page": 1, "quote": q} for i, (q, _) in enumerate(repeated)]
    url = f"/tests/fixtures/roundtrip/{case}/pdf/source.pdf"
    drawn = draw(harness, {"kind": "pdf", "url": url}, locations, {"name": "box", "padding": 0}, zoom=1)
    unresolved = {entry["location"]["id"]: entry["reason"] for entry in drawn["result"]["unresolved"]}
    rects: dict[str, list] = {}
    for mark in drawn["drawn"]:
        rects.setdefault(mark["id"], []).append(mark["rect"])
    for i, (quote, run) in enumerate(unique):
        assert f"u{i}" not in unresolved, f"{case}: {quote!r} is on the page once but was refused: {unresolved.get(f'u{i}')}"
        expected = sorted(line_hulls(run, width, height))
        got = sorted(rects[f"u{i}"])
        assert len(got) == len(expected), f"{case}: {quote!r} drew {len(got)} line rects, the page has {len(expected)}"
        for g, e in zip(got, expected):
            worst = max(abs(a - b) for a, b in zip(g, e))
            assert worst <= ANCHOR_TOLERANCE_PX, f"{case}: {quote!r} drawn {g} vs true {e} ({worst:.2f} px off)"
    for i, (quote, _) in enumerate(repeated):
        assert f"r{i}" in unresolved and f"r{i}" not in rects, f"{case}: {quote!r} occurs twice and must be refused, not picked"


def overlap(a, b) -> float:
    """Intersection over the smaller rect's area: 1.0 when one sits inside the other."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h / min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


def test_anchor_cjk_quote_is_found_where_the_text_layer_uses_lookalike_code_points(harness):
    """Chromium printed case 11 with Kangxi radicals in the text layer (⽐ U+2F50 for 比, ⼈ for 人; the harness's
    selfcheck lists 5). A reader's quote uses the ordinary character; NFKC folds one into the other."""
    arm = load_arm("11-mixed-chinese", "pdf")
    width, height = arm.page_size
    words = {w.text: w for w in arm.words}
    quotes = {"q1": "减速比", "q2": "通信接口"}
    locations = [{"id": key, "page": 1, "quote": quote} for key, quote in quotes.items()]
    url = "/tests/fixtures/roundtrip/11-mixed-chinese/pdf/source.pdf"
    drawn = draw(harness, {"kind": "pdf", "url": url}, locations, {"name": "box", "padding": 0}, zoom=1)
    assert drawn["result"]["unresolved"] == [], [entry["reason"] for entry in drawn["result"]["unresolved"]]
    rects = {mark["id"]: mark["rect"] for mark in drawn["drawn"]}
    true_box = line_hulls([words["减速比"]], width, height)[0]
    assert overlap(rects["q1"], true_box) >= 0.8, (rects["q1"], true_box)


# ---- 3. anchor: markdown text ranges mark exactly their characters, any span, every style (pixels) ------------------


def utf16(text: str, code_points: int) -> int:
    return len(text[:code_points].encode("utf-16-le")) // 2


RANGE_RECTS_JS = """([start, end]) => {
    const page = document.querySelector(".sh-page");
    const range = document.createRange();
    const node = page.querySelector(".sh-text").firstChild;
    range.setStart(node, start); range.setEnd(node, end);
    const origin = page.getBoundingClientRect();
    return [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0)
        .map((r) => [r.left - origin.left, r.top - origin.top, r.right - origin.left, r.bottom - origin.top]);
}"""


def page_shot(harness) -> np.ndarray:
    return pixels(Image.open(io.BytesIO(harness.locator(".sh-page").first.screenshot())))


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("case", ["04-multi-model", "11-mixed-chinese"])
def test_anchor_markdown_text_ranges_paint_exactly_their_characters(harness, case, style, seed):
    arm = load_arm(case, "pdf")
    source = {"kind": "text", "text": arm.markdown}
    spans = pick_spans(arm, seed, max_words=6)
    locations = [{"id": f"s{i}", "text": {"start": run[0].start, "end": run[-1].end}} for i, run in enumerate(spans)]
    draw(harness, source, [], "highlight", width=900)  # the plain display (an empty spotlight is all dark)
    baseline = page_shot(harness)
    targets = []
    for run in spans:  # the oracle: the browser's own layout of those characters, via a Range this test builds
        targets += harness.evaluate(RANGE_RECTS_JS, [utf16(arm.markdown, run[0].start), utf16(arm.markdown, run[-1].end)])
    drawn = draw(harness, source, locations, style, width=900)
    assert drawn["result"]["unresolved"] == []
    words = " | ".join(arm.markdown[run[0].start : run[-1].end] for run in spans)
    assert_visibly_marked(style, baseline, page_shot(harness), targets, units=1.0, label=f"{case}/markdown/{style} [{words}]")


# ---- 4. anchor: an exact html range lands on the true word, never on its twin in the next column -------------------


ORACLE_HTML_JS = """(words) => {
    const doc = document.querySelector("iframe").contentDocument;
    return words.map(({xpath, exact}) => {
        const element = doc.evaluate(xpath, doc, null, 9, null).singleNodeValue;
        if (!element) return null;
        const walker = doc.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        const hits = [];
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            for (let at = node.data.indexOf(exact); at >= 0; at = node.data.indexOf(exact, at + 1)) hits.push([node, at]);
        }
        if (hits.length !== 1) return null;  // not unique inside its element: no independent answer, skip it
        const range = doc.createRange();
        range.setStart(hits[0][0], hits[0][1]); range.setEnd(hits[0][0], hits[0][1] + exact.length);
        const r = range.getBoundingClientRect();
        return [r.left, r.top, r.right, r.bottom];
    });
}"""


@pytest.mark.parametrize("case", ["04-multi-model", "11-mixed-chinese"])
def test_anchor_html_source_range_alone_lands_on_the_true_word(harness, case):
    arm = load_arm(case, "html")
    words = [w for w in arm.words if arm.markdown.count(w.text) > 1][:40]  # repeated values: the datasheet trap
    locations = [{"id": f"w{w.index}", "html": {"source_range": w.grounding["html"]["source_range"]}} for w in words]
    url = f"/tests/fixtures/roundtrip/{case}/html/source.html"
    drawn = draw(harness, {"kind": "html", "url": url}, locations, {"name": "box", "padding": 0})
    assert drawn["result"]["unresolved"] == []
    truth = harness.evaluate(ORACLE_HTML_JS, [{"xpath": w.grounding["html"]["xpath"], "exact": w.text} for w in words])
    rects = {mark["id"]: mark["rect"] for mark in drawn["drawn"]}
    checked = 0
    for word, true_rect in zip(words, truth):
        if true_rect is None:
            continue
        got = rects[f"w{word.index}"]
        assert max(abs(a - b) for a, b in zip(got, true_rect)) <= 1.0, f"{case}: {word.text!r} drawn {got}, true {true_rect}"
        checked += 1
    assert checked >= 10, f"{case}: only {checked} repeated words had an independent answer"


# ---- 5. refuse loudly: what can't be placed is reported and nothing is painted for it ---------------------------------


def test_unplaceable_locations_are_refused_and_paint_nothing(harness, tmp_path):
    arm = load_arm("04-multi-model", "png")
    good = {"id": "good", "page": 1, "box": arm.words[0].grounding["box"]}
    bad = [
        {"id": "quote-on-image", "page": 1, "quote": "Rated torque"},  # an image has no text: OCR is the resolver's job
        {"id": "off-page", "page": 2, "box": [0.1, 0.1, 0.2, 0.2]},
        {"id": "inverted", "page": 1, "box": [0.5, 0.5, 0.4, 0.6]},
        {"id": "text-on-image", "text": {"start": 0, "end": 4}},
    ]
    only_good = pixels(render(arm.source, [good], "highlight", tmp_path / "good.png").outputs[0])
    result = render(arm.source, [good, *bad], "highlight", tmp_path / "all.png")
    assert sorted(entry.location["id"] for entry in result.unresolved) == sorted(b["id"] for b in bad)
    assert np.array_equal(pixels(result.outputs[0]), only_good), "a refused Location still painted something"
    md = load_arm("04-multi-model", "pdf").markdown
    drawn = draw(harness, {"kind": "text", "text": md}, [{"id": "twice", "quote": "24 V"}, {"id": "absent", "quote": "Rated flux"}], "highlight", width=900)
    assert {entry["location"]["id"] for entry in drawn["result"]["unresolved"]} == {"twice", "absent"}
    assert drawn["drawn"] == []


@pytest.mark.xfail(strict=True, reason="render() has no sha256 pin yet: a box made on other bytes paints anyway")
def test_a_box_made_on_different_bytes_is_refused(tmp_path):
    arm = load_arm("04-multi-model", "png")
    stale = {"id": "stale", "page": 1, "box": arm.words[30].grounding["box"], "sha256": "0" * 64}
    result = render(arm.source, [stale], "highlight", tmp_path / "out.png")
    assert [entry.location["id"] for entry in result.unresolved] == ["stale"]


# ---- 6. do no harm: idempotent, empty is identity, the output stays a valid document --------------------------------


def test_idempotent_identity_and_valid_output(tmp_path):
    arm = load_arm("11-mixed-chinese", "pdf")
    width, height = arm.page_size
    locations = [{"id": "a", "page": 1, "box": normalized(line_hulls(arm.words[5:9], width, height)[0], width, height)}]
    first = pixels(render(arm.source, locations, "box", tmp_path / "a.png", scale=PDF_SCALE).outputs[0])
    second = pixels(render(arm.source, locations, "box", tmp_path / "b.png", scale=PDF_SCALE).outputs[0])
    assert np.array_equal(first, second), "same input, different pixels"
    plain = render(arm.source, [], "box", tmp_path / "plain.png", scale=PDF_SCALE, page=1).outputs[0]
    assert np.array_equal(pixels(plain), baseline_of(arm.source, "pdf")[0]), "no Locations must mean the source, untouched"
    out_pdf = tmp_path / "marked.pdf"
    render(arm.source, locations, "spotlight", out_pdf)
    with pdfplumber.open(arm.source) as original, pdfplumber.open(out_pdf) as marked:
        assert len(marked.pages) == len(original.pages)
        assert marked.pages[0].extract_text() == original.pages[0].extract_text(), "marking changed the text layer"


# ---- 7. the ruler can fail: an off-by-one Location must turn the paint check red -----------------------------------


@pytest.mark.parametrize("style", STYLES)
def test_ruler_catches_a_neighbouring_word(tmp_path, style):
    """Mutation check of the oracle itself: paint the NEXT word (the classic off-by-one, or the twin value in the
    next model column) and the check must fail. If this passes silently, every green above means nothing."""
    arm = load_arm("04-multi-model", "png")
    width, height = arm.page_size
    run = [w for w in arm.words if w.text == "24"][:1]
    neighbour = [arm.words[run[0].index + 2]]  # the other "24" — the KM70-10 column
    assert neighbour[0].text == "24"
    wrong = [{"id": "x", "page": 1, "box": normalized(line_hulls(neighbour, width, height)[0], width, height)}]
    result = render(arm.source, wrong, style, tmp_path / "wrong.png")
    target = [[c * s for c, s in zip(run[0].grounding["box"], (width, height, width, height))]]
    with pytest.raises(AssertionError):
        assert_visibly_marked(style, pixels(arm.source), pixels(result.outputs[0]), target, units=1.0, label="mutant")


# ---- 8. budget: fast enough to use on every click ------------------------------------------------------------------


def test_performance_budget(harness, tmp_path):
    arm = load_arm("12-full-datasheet", "pdf")
    width, height = arm.page_size
    locations = [{"id": f"w{w.index}", "page": 1, "box": w.grounding["box"]} for w in arm.words]
    started = time.perf_counter()
    render(arm.source, locations, "spotlight", tmp_path / "all.png", scale=PDF_SCALE)
    static_s = time.perf_counter() - started
    text = arm.markdown
    text_locations = [{"id": f"w{w.index}", "text": {"start": w.start, "end": w.end}} for w in arm.words]
    started = time.perf_counter()
    draw(harness, {"kind": "text", "text": text}, text_locations, "highlight", width=900)
    interactive_s = time.perf_counter() - started
    print(f"budget: static {len(locations)} boxes {static_s:.2f} s; interactive {len(text_locations)} ranges {interactive_s:.2f} s")
    # WHY 1 s: a click on a value must not break the reader's flow (Nielsen's 1 s limit). Measured 2026-11-16 on a
    # loaded 24-core box: static 0.06-0.10 s, interactive 0.05-0.33 s; a tighter limit would flake under load.
    assert static_s <= 1.0, f"static render of {len(locations)} boxes took {static_s:.2f} s"
    assert interactive_s <= 1.0, f"drawing {len(text_locations)} ranges took {interactive_s:.2f} s"
