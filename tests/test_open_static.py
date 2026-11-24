"""open.html, the static entry point (CONTRACT §7): one stored source opened at a Location from a plain URL, with no
server — served by a plain static server and opened from file://, from the folder scripts/vendor.py writes.

Measured, never presence-asserted: each mark's getBoundingClientRect against a rectangle computed outside the module —
pdfplumber's character boxes for a PDF quote, pdfium page sizes for a box, Pillow image sizes, a DOM range the test
builds itself from the fixture's own xpath — and the page on screen against pdfium renders of every page.
"""

from __future__ import annotations

import base64
import functools
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageFilter

from source_highlighter.check import pdf_open_link

from .conftest import CUBEMARS_HTML, EROB_JPG, FIXTURES, REPO, load_locations

LRS_PDF = FIXTURES / "lrs-350.pdf"
PADDING = 2  # STYLE_DEFAULTS padding, source units
TOLERANCE_PX = 1.5
SHOTS = os.environ.get("SOURCE_HIGHLIGHTER_SHOTS")  # a folder: the acceptance screenshots are written there too
READY = "['1', 'error', 'blocked'].includes(document.body.dataset.ready)"


# ---- the static folder: the vendored bundle beside the sources it opens ---------------------------------------------


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    if not (REPO / "js" / "node_modules" / "pdfjs-dist").exists():
        pytest.skip("PDF.js not installed: run `pnpm --dir js install`")
    root = tmp_path_factory.mktemp("static-site")
    subprocess.run([sys.executable, str(REPO / "scripts" / "vendor.py"), "--worktree", "--into", str(root / "sh")], check=True, capture_output=True)
    (root / "sources").mkdir()
    for path in (LRS_PDF, CUBEMARS_HTML, EROB_JPG):
        shutil.copyfile(path, root / "sources" / path.name)
    return root


@pytest.fixture(scope="session")
def http_base(site):
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def file_chromium(chromium):
    """Headless Chromium as a reader opens a local file with --allow-file-access-from-files (Chrome's own switch):
    without it Chromium lets no file:// page read other files — test_stock_chromium_on_a_file_url_says_what_to_do."""
    browser = chromium.browser_type.launch(args=["--allow-file-access-from-files"])
    yield browser
    browser.close()


@pytest.fixture(params=["http", "file"])
def origin(request, site, chromium, file_chromium):
    if request.param == "http":
        return chromium, request.getfixturevalue("http_base")
    return file_chromium, site.as_uri()


def query(**params) -> str:
    return urllib.parse.urlencode([(key, item) for key, value in params.items() for item in (value if isinstance(value, list) else [value])])


def open_view(browser, base: str, params: dict, *, viewport=(1280, 800), dark=False, storage=None):
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=1,
                                  color_scheme="dark" if dark else "light")
    page = context.new_page()
    page.problems = []
    page.on("pageerror", lambda error: page.problems.append(f"pageerror: {error}"))
    page.on("console", lambda message: message.type in ("error", "warning") and page.problems.append(f"{message.type}: {message.text}"))
    if storage:
        # only where the reader has not chosen yet, so a reload keeps what the page itself remembered
        page.add_init_script(f"for (const [k, v] of Object.entries({json.dumps(storage)})) if (localStorage.getItem(k) === null) localStorage.setItem(k, v);")
    page.goto(f"{base}/sh/open.html?{query(**params)}")
    page.wait_for_function(READY, timeout=30000)
    return page


def shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"static-{name}.png"))


MEASURE = """() => {
    const stage = document.querySelector(".o-stage").getBoundingClientRect();
    const marks = [...document.querySelectorAll(".sh-mark")].map((mark) => {
        const page = mark.closest(".sh-page");
        const origin = page.getBoundingClientRect();
        const r = mark.getBoundingClientRect();
        return {id: mark.dataset.id, style: [...mark.classList].find((c) => c.startsWith("sh-") && c !== "sh-mark" && c !== "sh-focus"),
                page: Number(page.dataset.sourcePage || 1),
                rect: [r.left - origin.left, r.top - origin.top, r.right - origin.left, r.bottom - origin.top],
                screen: [r.left, r.top, r.right, r.bottom]};
    });
    const status = document.querySelector(".o-status");
    return {marks, stage: [stage.left, stage.top, stage.right, stage.bottom], zoom: window.sourceOpen?.state.view?.zoom ?? null,
            status: document.body.dataset.status, statusText: status ? status.textContent : "",
            pages: [...document.querySelectorAll(".sh-page")].map((p) => Number(p.dataset.sourcePage || 1)),
            where: [...document.querySelectorAll(".o-where li")].map((li) => li.textContent)};
}"""


def pdfplumber_quote(page_number: int, quote: str) -> list[float]:
    with pdfplumber.open(LRS_PDF) as pdf:
        found = pdf.pages[page_number - 1].search(quote, regex=False)
    assert len(found) == 1, (quote, found)
    return [found[0]["x0"], found[0]["top"], found[0]["x1"], found[0]["bottom"]]


def page_size(page_number: int) -> tuple[float, float]:
    document = pdfium.PdfDocument(str(LRS_PDF))
    try:
        return document[page_number - 1].get_size()
    finally:
        document.close()


def padded(rect, zoom, width, height):
    x0, y0, x1, y1 = rect
    return [max(0, x0 - PADDING) * zoom, max(0, y0 - PADDING) * zoom, min(width, x1 + PADDING) * zoom, min(height, y1 + PADDING) * zoom]


def assert_close(actual, expected, label) -> float:
    off = max(abs(a - b) for a, b in zip(actual, expected))
    assert off <= TOLERANCE_PX, f"{label}: drawn {actual} vs expected {expected} (off by {off:.2f} px)"
    return off


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def assert_in_view(measured, mark) -> None:
    """Landed on it: the mark lies wholly inside the stage the reader sees, without scrolling."""
    left, top, right, bottom = measured["stage"]
    x0, y0, x1, y1 = mark["screen"]
    assert left <= x0 and x1 <= right and top <= y0 and y1 <= bottom, f"mark {mark['screen']} is not inside the stage {measured['stage']}"


def _normalized(image: Image.Image) -> np.ndarray:
    small = np.asarray(image.convert("L").resize((120, 155), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.5)), dtype=float)
    small -= small.mean()
    return small / (np.linalg.norm(small) or 1.0)


def shown_pdf_page(page) -> int:
    """Which page of the PDF is on screen, by pixels: the drawn canvas correlated with pdfium renders of every page.
    (Measured on lrs-350.pdf: the right page correlates 0.85–0.98, the best wrong one at most 0.61.)"""
    data = page.evaluate("() => document.querySelector('.sh-page canvas').toDataURL('image/png')")
    canvas = Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGBA")
    drawn = _normalized(Image.alpha_composite(Image.new("RGBA", canvas.size, "white"), canvas))  # PDF.js leaves paper transparent
    document = pdfium.PdfDocument(str(LRS_PDF))
    try:
        scores = [float((drawn * _normalized(document[index].render(scale=0.5).to_pil())).sum()) for index in range(len(document))]
    finally:
        document.close()
    ranked = sorted(scores, reverse=True)
    assert ranked[0] > 0.8 and ranked[0] - ranked[1] > 0.2, f"no page clearly matches the canvas: {scores}"
    return scores.index(ranked[0]) + 1


# ---- PDF ----------------------------------------------------------------------------------------------------------


def test_pdf_page_and_quote_lands_on_the_cited_words(origin):
    browser, base = origin
    page = open_view(browser, base, {"src": "../sources/lrs-350.pdf", "where": "page=2&text=349.2W", "label": "Rated power — LRS-350-36",
                                      "url": "https://www.meanwell.com/LRS-350-SPEC.PDF", "retrieved": "2026-10-29T19:44:27Z"})
    measured = page.evaluate(MEASURE)
    assert measured["status"] == "marked" and measured["statusText"] == "Marked on page 2"
    assert measured["pages"] == [2] and shown_pdf_page(page) == 2
    (mark,) = measured["marks"]
    width, height = page_size(2)
    expected = padded(pdfplumber_quote(2, "349.2W"), measured["zoom"], width, height)
    off = assert_close(mark["rect"], expected, "349.2W")
    assert iou(mark["rect"], expected) > 0.85
    assert_in_view(measured, mark)
    # the live link opens the maker's PDF at that page with the words searched, exactly as `check` writes it
    live = page.evaluate("() => document.querySelector('.o-live').href")
    assert live == pdf_open_link("https://www.meanwell.com/LRS-350-SPEC.PDF", 2, "349.2W")
    assert page.evaluate("() => document.querySelector('.o-stored').textContent") == "stored ↗ 2026-10-29"
    print(f"pdf page+quote via {base.split(':')[0]}: off {off:.2f} px, iou {iou(mark['rect'], expected):.3f}, zoom {measured['zoom']:.3f}")
    shot(page, f"pdf-quote-{base.split(':')[0]}")
    assert page.problems == []
    page.context.close()


def test_pdf_box_and_quote_both_marked(origin):
    """A value with a measured box and the source's words: a box around the cell, the words highlighted inside it."""
    browser, base = origin
    width, height = page_size(2)
    words = pdfplumber_quote(2, "349.2W")
    # the cell around the words, as a reader measured it: a little wider than the words
    box = [(words[0] - 6) / width, (words[1] - 3) / height, (words[2] + 6) / width, (words[3] + 3) / height]
    where = f"page=2&xywh=percent:{box[0] * 100:.4f},{box[1] * 100:.4f},{(box[2] - box[0]) * 100:.4f},{(box[3] - box[1]) * 100:.4f}"
    page = open_view(browser, base, {"src": "../sources/lrs-350.pdf", "where": where, "quote": "349.2W", "label": "Rated power — LRS-350-36"})
    measured = page.evaluate(MEASURE)
    assert measured["status"] == "marked", measured
    by_style = {mark["style"]: mark for mark in measured["marks"]}
    assert set(by_style) == {"sh-box", "sh-highlight"}
    zoom = measured["zoom"]
    assert_close(by_style["sh-box"]["rect"], padded([box[0] * width, box[1] * height, box[2] * width, box[3] * height], zoom, width, height), "box")
    assert_close(by_style["sh-highlight"]["rect"], padded(words, zoom, width, height), "words")
    inner, outer = by_style["sh-highlight"]["rect"], by_style["sh-box"]["rect"]
    assert outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]
    shot(page, f"pdf-box-and-quote-{base.split(':')[0]}")
    assert page.problems == []
    page.context.close()


def test_ambiguous_quote_marks_nothing_and_says_so(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "where": "page=2&text=14.6A", "label": "Rated current"})
    measured = page.evaluate(MEASURE)
    assert measured["marks"] == []
    assert measured["status"] == "not-located"
    assert measured["statusText"] == 'Not located: ambiguous: "14.6A" occurs 2 times on page 2'
    shot(page, "pdf-ambiguous")
    assert page.problems == []
    page.context.close()


def test_what_cannot_be_placed_is_named_in_plain_words(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "where": ["page=2&text=349.2W", "table=1&row=4&col=9", "page=2&text=1234.5W", "page=two"]},
                     storage={"sh-open:details": "open"})
    measured = page.evaluate(MEASURE)
    assert measured["status"] == "partly" and measured["statusText"] == "Marked on page 2 · 3 not located"
    where = measured["where"]
    assert where[0].startswith("page=2&text=349.2WMarked on page 2")
    assert "not located: a table cell on a pdf needs its box" in where[1]
    assert 'not located: "1234.5W" is not in the source on page 2' in where[2]
    assert "not located: cannot read it: page must be a whole number" in where[3]
    assert len(measured["marks"]) == 1
    assert page.problems == []
    page.context.close()


def test_a_quote_with_no_page_is_found_on_its_page(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "quote": "349.2W"})
    measured = page.evaluate(MEASURE)
    assert measured["pages"] == [1, 2, 3, 4]  # no page given: every page is drawn and searched
    (mark,) = measured["marks"]
    assert mark["page"] == 2 and measured["statusText"] == "Marked on page 2"
    width, height = page_size(2)
    assert_close(mark["rect"], padded(pdfplumber_quote(2, "349.2W"), measured["zoom"], width, height), "quote anywhere")
    assert_in_view(measured, mark)
    page.context.close()


def test_page_arrows_walk_the_pdf_and_the_answer_stays(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "where": "page=2&text=349.2W"})
    assert page.evaluate("() => document.querySelector('.o-pos').textContent") == "page 2 of 4"
    page.click(".o-next")
    page.wait_for_function("document.body.dataset.ready === '1' && document.querySelector('.o-pos').textContent === 'page 3 of 4'")
    assert shown_pdf_page(page) == 3
    measured = page.evaluate(MEASURE)
    assert measured["marks"] == [] and measured["statusText"] == "Marked on page 2"  # the answer is about the cited page
    assert page.evaluate("() => document.querySelector('.o-pages').classList.contains('o-away')")
    page.keyboard.press("ArrowLeft")
    page.wait_for_function("document.body.dataset.ready === '1' && document.querySelector('.o-pos').textContent === 'page 2 of 4'")
    assert len(page.evaluate(MEASURE)["marks"]) == 1 and shown_pdf_page(page) == 2
    assert page.problems == []
    page.context.close()


def test_a_box_measured_on_other_bytes_is_refused_and_the_copy_is_checked(chromium, http_base):
    real = __import__("hashlib").sha256(LRS_PDF.read_bytes()).hexdigest()
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "where": "page=2&xywh=percent:67.9,12.9,5.3,1.4&sha256=" + "0" * 64,
                                           "sha256": real}, storage={"sh-open:details": "open"})
    measured = page.evaluate(MEASURE)
    assert measured["marks"] == [] and "measured on other bytes" in measured["statusText"]
    assert "matches the cited copy" in page.evaluate("() => document.querySelector('.o-source').textContent")
    page.context.close()
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "where": "page=2&text=349.2W", "sha256": "f" * 64})
    measured = page.evaluate(MEASURE)
    assert len(measured["marks"]) == 1 and measured["statusText"].endswith("the stored bytes are not the cited ones")
    assert page.evaluate("() => document.querySelector('.o-status').className") == "o-status o-warn"
    page.context.close()


# ---- HTML and image -------------------------------------------------------------------------------------------------


ORACLE_HTML = """([xpath, text]) => {
    const page = document.querySelector(".sh-page");
    const frame = page.querySelector("iframe").contentDocument;
    let cell = frame.evaluate(xpath, frame, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    const walker = frame.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const at = node.data.indexOf(text);
        if (at === -1) continue;
        const range = frame.createRange();
        range.setStart(node, at);
        range.setEnd(node, at + text.length);
        const r = range.getBoundingClientRect();
        return [r.left, r.top, r.right, r.bottom];  // the frame's own pixels, which the page shows scaled by the zoom
    }
    return null;
}"""


def test_html_value_lands_on_its_words(origin):
    browser, base = origin
    locations = {location["id"]: location for location in load_locations("cubemars-ak80-9.html.locations.json")}
    value = locations["rated-torque-value"]
    page = open_view(browser, base, {"src": "../sources/cubemars-ak80-9.html", "where": json.dumps({"html": value["html"]}),
                                      "quote": "Rated torque (Nm)", "label": "Rated torque", "url": "https://www.cubemars.com/product/ak80-9.html"})
    measured = page.evaluate(MEASURE)
    assert measured["status"] == "marked", measured
    zoom = measured["zoom"]
    marks = {mark["id"]: mark for mark in measured["marks"]}
    for mark_id, location, text in (("where-1", value, "9"), ("quote", locations["rated-torque-label"], "Rated torque (Nm)")):
        xpath = location["html"]["xpath"]
        oracle = page.evaluate(ORACLE_HTML, [xpath, text])
        expected = [(oracle[0] - PADDING) * zoom, (oracle[1] - PADDING) * zoom, (oracle[2] + PADDING) * zoom, (oracle[3] + PADDING) * zoom]
        assert_close(marks[mark_id]["rect"], expected, text)
    assert_in_view(measured, marks["where-1"])
    live = page.evaluate("() => document.querySelector('.o-live').href")
    assert live == "https://www.cubemars.com/product/ak80-9.html#:~:text=Rated%20torque,%28Nm%29"
    shot(page, f"html-{base.split(':')[0]}")
    assert page.problems == []
    page.context.close()


def test_html_compact_quote_with_its_context(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/cubemars-ak80-9.html", "where": "text=Rated%20torque%20(Nm)-,9,-Outer"})
    measured = page.evaluate(MEASURE)
    (mark,) = measured["marks"]
    xpath = load_locations("cubemars-ak80-9.html.locations.json")[1]["html"]["xpath"]
    oracle = page.evaluate(ORACLE_HTML, [xpath, "9"])
    zoom = measured["zoom"]
    assert_close(mark["rect"], [(oracle[0] - PADDING) * zoom, (oracle[1] - PADDING) * zoom, (oracle[2] + PADDING) * zoom, (oracle[3] + PADDING) * zoom], "9")
    page.context.close()


def test_image_box_lands_on_its_box(origin):
    browser, base = origin
    location = {loc["id"]: loc for loc in load_locations("erob80-band.locations.json")}["rated-torque-erob80i-50"]
    x0, y0, x1, y1 = location["box"]
    where = f"page=1&xywh=percent:{x0 * 100},{y0 * 100},{(x1 - x0) * 100},{(y1 - y0) * 100}"
    page = open_view(browser, base, {"src": "../sources/erob80-band.jpg", "where": where, "quote": "额定扭矩", "label": "eRob80I-50 rated torque"},
                     storage={"sh-open:details": "open"})
    measured = page.evaluate(MEASURE)
    assert measured["status"] == "marked"
    (mark,) = measured["marks"]
    width, height = Image.open(EROB_JPG).size
    assert_close(mark["rect"], padded([x0 * width, y0 * height, x1 * width, y1 * height], measured["zoom"], width, height), "image box")
    assert_in_view(measured, mark)
    # an image has no text: the quote is shown, not searched, and is not counted as "not located"
    assert "an image has no text to search" in measured["where"][1]
    shot(page, f"image-{base.split(':')[0]}")
    assert page.problems == []
    page.context.close()


# ---- the page itself: embed, phone + dark, the details boundary, no source, file:// without permission -----------------


def test_embed_shows_only_the_source_and_the_way_out(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "where": "page=2&text=349.2W", "embed": "1"}, viewport=(420, 260))
    assert page.evaluate("() => getComputedStyle(document.querySelector('.o-head')).display") == "none"
    measured = page.evaluate(MEASURE)
    assert_in_view(measured, measured["marks"][0])
    link = page.evaluate("() => { const a = document.querySelector('.o-open-full'); return [a.hidden, a.href, a.textContent]; }")
    assert link[0] is False and "embed" not in link[1] and "src=" in link[1] and link[2] == "Open ↗"
    shot(page, "embed")
    page.context.close()


def test_phone_width_and_dark_mode(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "where": "page=2&text=349.2W", "label": "Rated power — LRS-350-36",
                                           "quote": "349.2W", "url": "https://www.meanwell.com/LRS-350-SPEC.PDF"}, viewport=(375, 740), dark=True)
    measured = page.evaluate(MEASURE)
    head = page.evaluate("() => { const h = document.querySelector('.o-head'); return [h.scrollWidth, h.clientWidth, getComputedStyle(document.body).backgroundColor]; }")
    assert head[0] <= head[1] + 1, f"the header overflows sideways at 375 px: {head}"
    assert head[2] == "rgb(13, 17, 23)"
    assert measured["zoom"] >= 1  # readable: the cited words at their printed size, scrolled to, not a fitted page
    assert_in_view(measured, measured["marks"][0])
    shot(page, "phone-dark")
    assert page.problems == []
    page.context.close()


def test_details_boundary_drags_and_is_remembered(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/lrs-350.pdf", "where": "page=2&text=349.2W"}, storage={"sh-open:details": "open"})
    split = page.evaluate("() => { const r = document.querySelector('.o-split').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }")
    before = page.evaluate("() => document.querySelector('.o-details').getBoundingClientRect().width")
    page.mouse.move(*split)
    page.mouse.down()
    page.mouse.move(split[0] - 120, split[1], steps=6)
    page.mouse.up()
    after = page.evaluate("() => document.querySelector('.o-details').getBoundingClientRect().width")
    assert abs(after - (before + 120)) <= 1
    page.reload()
    page.wait_for_function(READY)
    assert abs(page.evaluate("() => document.querySelector('.o-details').getBoundingClientRect().width") - after) <= 1
    page.click(".o-details-toggle")
    assert page.evaluate("() => document.querySelector('.o-details').getBoundingClientRect().width") == 0
    page.reload()
    page.wait_for_function(READY)
    assert page.evaluate("() => document.querySelector('.o-details').getBoundingClientRect().width") == 0  # the fold is remembered
    page.context.close()


def test_no_source_says_what_to_give(chromium, http_base):
    page = open_view(chromium, http_base, {})
    text = page.evaluate("() => document.querySelector('.o-host').textContent")
    assert "Nothing to open yet." in text and "open.html?src=" in text
    page.context.close()


def test_a_missing_stored_copy_says_so(chromium, http_base):
    page = open_view(chromium, http_base, {"src": "../sources/gone.html", "where": "text=x"})
    assert page.evaluate("() => document.body.dataset.status") == "error"
    assert page.evaluate("() => document.querySelector('.o-status').textContent") == "Can't open the stored copy: could not fetch " \
        + f"{http_base}/sources/gone.html: HTTP 404"
    page.context.close()


def test_stock_chromium_on_a_file_url_says_what_to_do(chromium, site):
    page = open_view(chromium, site.as_uri(), {"src": "../sources/lrs-350.pdf", "where": "page=2&text=349.2W"})
    assert page.evaluate("() => document.body.dataset.ready") == "blocked"
    text = page.evaluate("() => document.querySelector('.o-host').textContent")
    assert "this browser does not let it read other files" in text
    assert f'--directory "{site}"' in text and "http://127.0.0.1:8000/sh/open.html?src=" in text
    assert page.evaluate("() => document.querySelector('.o-host a').href") == (site / "sources" / "lrs-350.pdf").as_uri() + "#page=2"
    shot(page, "file-blocked")
    page.context.close()


def test_a_drawing_with_no_text_layer_is_marked_by_its_box(chromium, http_base, site):
    """The J60 sheet is vector art with no text: its box marks the value and its words are shown, not searched; a
    quote alone says why it cannot be placed instead of claiming the words are missing."""
    shutil.copyfile(FIXTURES / "j60-joint.pdf", site / "sources" / "j60-joint.pdf")
    location = {loc["id"]: loc for loc in load_locations("j60-joint.locations.json")}["peak-torque-j60-6"]
    x0, y0, x1, y1 = location["box"]
    where = f"page=2&xywh=percent:{x0 * 100},{y0 * 100},{(x1 - x0) * 100},{(y1 - y0) * 100}"
    page = open_view(chromium, http_base, {"src": "../sources/j60-joint.pdf", "where": where, "quote": "19.94N.m", "label": "J60-6 peak torque"},
                     storage={"sh-open:details": "open"})
    measured = page.evaluate(MEASURE)
    assert measured["status"] == "marked" and measured["statusText"] == "Marked on page 2" and len(measured["marks"]) == 1
    assert "page 2 has no text layer (a drawing or a scan): only a page box can mark it" in measured["where"][1]
    assert "not located" not in measured["where"][1]
    shot(page, "pdf-drawing-box")
    page.context.close()
    page = open_view(chromium, http_base, {"src": "../sources/j60-joint.pdf", "where": "page=2&text=19.94N.m"})
    assert page.evaluate(MEASURE)["statusText"] == "Not located: page 2 has no text layer (a drawing or a scan): only a page box can mark it"
    page.context.close()
