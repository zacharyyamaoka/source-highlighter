"""The standalone viewer (`source-highlighter view`): the server, Lens (one reference, full page) and Peek (citations
in someone else's page), driven in headless Chromium and measured — mark rects against their Locations, panel
boundaries dragged and re-measured after a reload, and the console kept empty."""

from __future__ import annotations

import functools
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pdfplumber
import pypdfium2 as pdfium
import pytest

from source_highlighter.view import make_server

from .conftest import CUBEMARS_HTML, FIXTURES, REPO, load_locations

TOLERANCE_PX = 1.0
LRS_PDF = FIXTURES / "lrs-350.pdf"
NOTE = FIXTURES / "vault" / "Bench notes.md"
PDF_BOX = [0.67946, 0.12915, 0.7325, 0.14301]  # LRS-350-24 rated power, page 2
LENS_PDF_ZOOM = 1.5
SPOTLIGHT_PADDING = 2
# tests/fixtures/peek-host.html cites the babble fixture's ids; these are the same kinds of reference in this set
HOST_IDS = {"ref-1": "html", "ref-2": "pdf-box", "ref-3": "quote-only", "ref-4": "pdf-page", "ref-5": "link-only", "ref-6": "pdf-quote", "ref-7": "note"}


def reference_set(tmp_path) -> tuple:
    """A reference set over committed fixtures: every Location kind, a quote-only reference and a link-only one."""
    html_locations = load_locations("cubemars-ak80-9.html.locations.json")[:2]
    refs = [
        {"id": "html", "label": "Rated torque — CubeMars AK80-9", "source": {"url": "https://www.cubemars.com/product/ak80-9.html",
         "retrieved_at": "2026-10-29T19:44:27Z", "stored": {"html": str(CUBEMARS_HTML)}}, "locations": html_locations, "quote": "Rated torque (Nm) 9",
         "where": "Specifications table, row “Rated torque (Nm)”"},
        {"id": "pdf-box", "label": "Rated power — LRS-350-24", "source": {"url": "https://www.meanwell.com/LRS-350-SPEC.PDF", "retrieved_at": "2026-10-29T19:44:27Z",
         "stored": {"pdf": str(LRS_PDF)}}, "locations": [{"id": "rated-power-24", "page": 2, "box": PDF_BOX}], "quote": "350.4W"},
        {"id": "pdf-quote", "label": "Rated power — LRS-350-48", "source": {"url": "https://www.meanwell.com/LRS-350-SPEC.PDF", "stored": {"pdf": str(LRS_PDF)}},
         "locations": [{"id": "rated-48", "page": 2, "quote": "349.2W"}]},
        {"id": "pdf-page", "label": "Block diagram — LRS-350", "source": {"url": "https://www.meanwell.com/LRS-350-SPEC.PDF", "stored": {"pdf": str(LRS_PDF)}},
         "locations": [{"id": "page-3", "page": 3}]},
        {"id": "quote-only", "label": "Efficiency — LRS-350-36", "source": {"url": "https://www.meanwell.com/LRS-350-SPEC.PDF", "stored": {"pdf": str(LRS_PDF)}},
         "locations": [], "quote": "88.5%"},
        {"id": "note", "label": "Torque reasoning", "source": {"vault": NOTE.name}, "locations": [{"id": "torque", "note": {"heading": "Torque"}}]},
        {"id": "link-only", "label": "Peak torque — qdd100", "source": {"url": "https://mjbots.com/products/qdd100-beta-3", "stored": {}}, "locations": [],
         "quote": "Peak torque 100 N·m"},
    ]
    statuses = {"checked_at": "2026-11-05T19:31:00Z", "statuses": {
        "html": {"status": "live", "label": "live · unchanged since 2026-10-29", "why": "every location resolves on the live page and the quote is found exactly once",
                 "checked_at": "2026-11-05T19:31:00Z", "live_link": "https://www.cubemars.com/product/ak80-9.html#:~:text=Rated%20torque%20%28Nm%29,9"},
        "pdf-box": {"status": "deviated", "label": "page changed since 2026-10-29", "why": "the live file's sha256 no longer matches the cited one",
                    "checked_at": "2026-11-05T19:31:02Z", "live_link": "https://www.meanwell.com/LRS-350-SPEC.PDF#page=2"},
        "pdf-page": {"status": "dead", "label": "page unreachable", "why": "HTTP 404; failed on 2026-11-01, 2026-11-03, 2026-11-05", "checked_at": "2026-11-05T19:31:03Z"},
    }}
    refs_path = tmp_path / "refs.json"
    refs_path.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
    statuses_path = tmp_path / "statuses.json"
    statuses_path.write_text(json.dumps(statuses), encoding="utf-8")
    return refs_path, statuses_path


@pytest.fixture
def viewer(tmp_path):
    if not (REPO / "js" / "node_modules" / "pdfjs-dist").exists():
        pytest.skip("PDF.js not installed: run `pnpm --dir js install`")
    refs_path, _ = reference_set(tmp_path)
    server = make_server(refs_path, vault=FIXTURES / "vault")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@pytest.fixture
def host_site(viewer, tmp_path):
    """The plain host page on ANOTHER origin, pointing at the viewer: Peek is embedded cross-origin."""
    site = tmp_path / "host"
    site.mkdir()
    page = (FIXTURES / "peek-host.html").read_text(encoding="utf-8").replace("__VIEWER__", viewer)
    for babble_id, ref_id in HOST_IDS.items():
        page = page.replace(f'data-ref="{babble_id}"', f'data-ref="{ref_id}"')
    page = page.replace("</main>", '<p>And a citation the set does not name: <span data-ref="nope">unknown</span>.</p></main>')
    (site / "index.html").write_text(page, encoding="utf-8")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@pytest.fixture
def browser_page(chromium):
    context = chromium.new_context(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
    page = context.new_page()
    page.console_messages = []
    page.on("console", lambda message: page.console_messages.append(f"{message.type}: {message.text}"))
    page.on("pageerror", lambda error: page.console_messages.append(f"pageerror: {error}"))
    yield page
    context.close()


def get(url, origin=None):
    request = urllib.request.Request(url, headers={"Origin": origin} if origin else {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), b""


def open_lens(page, viewer, ref_id):
    page.goto(f"{viewer}#ref={ref_id}")
    page.wait_for_function("document.querySelector('.l-stage').dataset.ready === '1' || document.querySelector('.l-stage .sh-v-empty')", timeout=20000)


MARK_RECTS = """(id) => [...document.querySelectorAll(`.l-stage .sh-mark[data-id="${id}"]`)].map((mark) => {
    const origin = mark.closest('.sh-page').getBoundingClientRect();
    const r = mark.getBoundingClientRect();
    return [r.left - origin.left, r.top - origin.top, r.right - origin.left, r.bottom - origin.top];
})"""


def assert_close(actual, expected, label):
    off = max(abs(a - b) for a, b in zip(actual, expected))
    assert off <= TOLERANCE_PX, f"{label}: drawn {actual} vs expected {expected} (off by {off:.2f} px)"
    return off


# ---- the server ---------------------------------------------------------------------------------------------------


def test_server_hands_out_only_the_files_the_set_names(viewer):
    status, headers, body = get(viewer + "stored/html/html")
    assert status == 200 and body == CUBEMARS_HTML.read_bytes()
    assert "sandbox" in headers["Content-Security-Policy"]  # opening the archived page never runs its scripts
    assert get(viewer + "stored/pdf-box/pdf")[2] == LRS_PDF.read_bytes()
    assert get(viewer + "stored/note/vault")[2] == NOTE.read_bytes()
    for path in ("stored/html/pdf", "stored/nope/html", "stored/html/..%2F..%2Fetc%2Fpasswd", "../pyproject.toml", "stored/link-only/html"):
        assert get(viewer + path)[0] == 404, path
    state = json.loads(get(viewer + "api/state")[2])
    assert state["files"]["html"]["html"] == {"kept": True, "bytes": CUBEMARS_HTML.stat().st_size}
    assert state["statuses"]["pdf-box"]["status"] == "deviated" and state["pdfjs"] is True
    # a page on another localhost port may embed Peek; no other site may read the stored files
    assert get(viewer + "refs.json", "http://127.0.0.1:9")[1].get("Access-Control-Allow-Origin") == "http://127.0.0.1:9"
    assert "Access-Control-Allow-Origin" not in get(viewer + "refs.json", "https://evil.example")[1]


def test_cli_view_prints_its_url_and_serves(tmp_path):
    refs_path, _ = reference_set(tmp_path)
    process = subprocess.Popen([sys.executable, "-m", "source_highlighter.cli", "view", str(refs_path), "--port", "0"], stdout=subprocess.PIPE, text=True)
    try:
        url = process.stdout.readline().strip()
        assert url.startswith("http://127.0.0.1:") and url.endswith("/")
        status, _, body = get(url)
        assert status == 200 and b"lens.js" in body
    finally:
        process.terminate()  # by the PID we started, never by port
        process.wait(timeout=10)


# ---- Lens: every Location kind lands where it says ----------------------------------------------------------------


def test_lens_marks_land_on_their_locations(viewer, browser_page):
    page = browser_page
    worst = 0.0
    document = pdfium.PdfDocument(str(LRS_PDF))
    sizes = {number: document[number - 1].get_size() for number in (2, 3)}
    document.close()

    open_lens(page, viewer, "pdf-box")
    width, height = sizes[2]
    x0, y0, x1, y1 = PDF_BOX
    expected = [(x0 * width - SPOTLIGHT_PADDING) * LENS_PDF_ZOOM, (y0 * height - SPOTLIGHT_PADDING) * LENS_PDF_ZOOM,
                (x1 * width + SPOTLIGHT_PADDING) * LENS_PDF_ZOOM, (y1 * height + SPOTLIGHT_PADDING) * LENS_PDF_ZOOM]
    worst = max(worst, assert_close(page.evaluate(MARK_RECTS, "rated-power-24")[0], expected, "box"))

    for ref_id, location_id, quote in (("pdf-quote", "rated-48", "349.2W"), ("quote-only", "quote", "88.5%")):
        open_lens(page, viewer, ref_id)
        with pdfplumber.open(LRS_PDF) as pdf:
            hit = pdf.pages[1].search(quote, regex=False)[0]
        expected = [(value + pad) * LENS_PDF_ZOOM for value, pad in zip((hit["x0"], hit["top"], hit["x1"], hit["bottom"]), (-2, -2, 2, 2))]
        worst = max(worst, assert_close(page.evaluate(MARK_RECTS, location_id)[0], expected, ref_id))

    open_lens(page, viewer, "pdf-page")
    width, height = sizes[3]
    worst = max(worst, assert_close(page.evaluate(MARK_RECTS, "page-3")[0], [0, 0, width * LENS_PDF_ZOOM, height * LENS_PDF_ZOOM], "page-only"))

    open_lens(page, viewer, "html")
    for location in load_locations("cubemars-ak80-9.html.locations.json")[:2]:
        cell = page.evaluate(
            """(xpath) => { const d = document.querySelector('.l-stage iframe').contentDocument;
                const cell = d.evaluate(xpath, d, null, 9, null).singleNodeValue; const range = d.createRange();
                range.selectNodeContents(cell); const r = range.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }""",
            location["html"]["xpath"],
        )
        expected = [cell[0] - 2, cell[1] - 2, cell[2] + 2, cell[3] + 2]  # Lens shows html at zoom 1
        worst = max(worst, assert_close(page.evaluate(MARK_RECTS, location["id"])[0], expected, location["id"]))

    open_lens(page, viewer, "note")
    text = NOTE.read_text(encoding="utf-8")
    start = text.index("## Torque\n")
    end = len(text[: text.index("## Open questions")].rstrip())
    expected_lines = page.evaluate(
        """([start, end]) => { const page = document.querySelector('.l-stage .sh-page'); const node = page.querySelector('.sh-text').firstChild;
            const range = document.createRange(); range.setStart(node, start); range.setEnd(node, end); const o = page.getBoundingClientRect();
            const zoom = page.getBoundingClientRect().width / page.querySelector('.sh-text').offsetWidth;
            return [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0)
              .map((r) => [r.left - o.left - 2 * zoom, r.top - o.top - 2 * zoom, r.right - o.left + 2 * zoom, r.bottom - o.top + 2 * zoom]); }""",
        [start, end],
    )
    drawn = page.evaluate(MARK_RECTS, "torque")
    assert len(drawn) == len(expected_lines) > 5
    for drawn_rect, expected_rect in zip(drawn, expected_lines):
        worst = max(worst, assert_close(drawn_rect, expected_rect, "note"))

    open_lens(page, viewer, "link-only")
    assert page.locator(".l-stage .sh-v-empty").count() == 1 and page.locator(".l-stage .sh-mark").count() == 0
    print(f"lens marks: worst {worst:.3f} px")
    assert page.console_messages == []


def test_lens_shows_one_answer_first_and_details_on_demand(viewer, browser_page, tmp_path):
    page = browser_page
    open_lens(page, viewer, "html")
    # one answer first: the pill, both links; Details folded
    assert page.locator(".l-head .sh-v-pill").get_attribute("data-status") == "live"  # from the statuses.json beside the refs
    assert page.locator(".l-details").is_hidden()
    page.click(".l-details-toggle")
    assert page.locator(".l-details").is_visible()
    chain = page.evaluate("() => [...document.querySelectorAll('.l-chain .l-node')].map((n) => [n.dataset.level, n.className])")
    assert [level for level, _ in chain] == ["live", "html", "snapshot", "archive", "pdf"]
    assert "shown" in dict(chain)["html"] and "not" in dict(chain)["pdf"]
    assert page.locator(".l-chain").inner_text().count("not kept") == 3
    page.reload()
    page.wait_for_function("document.querySelector('.l-stage').dataset.ready === '1'")
    assert page.locator(".l-details").is_visible()  # the fold is remembered
    page.keyboard.press("ArrowRight")
    page.wait_for_function("location.hash === '#ref=pdf-box' && document.querySelector('.l-stage').dataset.ready === '1'")
    assert page.locator(".l-pos").inner_text() == "2 / 7"


def test_lens_status_from_the_check_and_calm_colours(tmp_path, chromium):
    refs_path, statuses_path = reference_set(tmp_path)
    server = make_server(refs_path, statuses=statuses_path, vault=FIXTURES / "vault")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    context = chromium.new_context(viewport={"width": 1400, "height": 900})
    page = context.new_page()
    try:
        tones = {}
        for ref_id in ("html", "pdf-box", "pdf-page", "pdf-quote", "link-only"):
            open_lens(page, url, ref_id)
            pill = page.locator(".l-head .sh-v-pill")
            tones[ref_id] = (pill.get_attribute("data-status"), pill.get_attribute("class").split()[-1], pill.inner_text())
        assert tones["html"] == ("live", "sh-v-ok", "live · unchanged since 2026-10-29")
        assert tones["pdf-box"] == ("deviated", "sh-v-warn", "page changed since 2026-10-29")
        assert tones["pdf-page"] == ("dead", "sh-v-bad", "page unreachable")
        assert tones["pdf-quote"][:2] == ("unchecked", "sh-v-quiet") and tones["link-only"][:2] == ("link-only", "sh-v-quiet")
        open_lens(page, url, "html")
        page.click(".l-details-toggle")
        details = page.locator(".l-details").inner_text()
        assert "every location resolves" in details and "checked 2026-11-05 19:31:00 UTC" in details
        live = page.locator(".l-both a").first.get_attribute("href")
        assert live.endswith("#:~:text=Rated%20torque%20%28Nm%29,9")
        stored = page.locator(".l-both a").nth(1).get_attribute("href")
        assert stored == url + "stored/html/html"
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_lens_details_boundary_drags_and_is_remembered(viewer, browser_page):
    page = browser_page
    open_lens(page, viewer, "pdf-box")
    page.click(".l-details-toggle")
    before = page.locator(".l-details").bounding_box()["width"]
    split = page.locator(".l-split").bounding_box()
    x, y = split["x"] + split["width"] / 2, split["y"] + split["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 60, y, steps=4)
    page.mouse.move(x - 120, y, steps=4)
    page.mouse.up()
    after = page.locator(".l-details").bounding_box()["width"]
    assert after - before == pytest.approx(120, abs=1)
    stage = page.locator(".l-stage").bounding_box()
    assert stage["x"] + stage["width"] == pytest.approx(page.locator(".l-split").bounding_box()["x"], abs=1)
    page.reload()
    page.wait_for_function("document.querySelector('.l-stage').dataset.ready === '1'")
    assert page.locator(".l-details").bounding_box()["width"] == pytest.approx(after, abs=1)
    print(f"lens details drag: {before:.0f} -> {after:.0f} px, after reload {page.locator('.l-details').bounding_box()['width']:.0f} px")


# ---- Peek: citations in a plain host page on another origin --------------------------------------------------------


def wait_peek(page):
    page.wait_for_function("window.sourcePeek !== undefined", timeout=20000)


def test_peek_hover_crop_click_pin_open_full(host_site, viewer, browser_page):
    """A plain page on another origin: hover → a crop centred on the mark; click → a pinned panel; Open full → Lens."""
    page = browser_page
    page.goto(host_site)
    wait_peek(page)
    assert page.locator(".shp-cite").count() == 8
    assert page.locator(".shp-cite.shp-missing").count() == 1  # "nope": marked as unknown, never guessed
    scroll_before = page.evaluate("() => [scrollX, scrollY]")

    page.hover('[data-ref="pdf-box"]')
    page.wait_for_function("document.querySelector('.shp-card .shp-crop')?.dataset.ready === '1'", timeout=20000)
    crop = page.evaluate(
        """() => { const c = document.querySelector('.shp-crop').getBoundingClientRect(); const m = document.querySelector('.shp-crop .sh-mark').getBoundingClientRect();
                   return {c: [c.left, c.top, c.right, c.bottom], m: [m.left, m.top, m.right, m.bottom]}; }"""
    )
    c, m = crop["c"], crop["m"]
    assert c[0] <= m[0] and m[2] <= c[2] and c[1] <= m[1] and m[3] <= c[3]  # the mark is inside the crop
    assert abs((m[1] + m[3]) / 2 - (c[1] + c[3]) / 2) <= 2 and abs((m[0] + m[2]) / 2 - (c[0] + c[2]) / 2) <= 2  # in its middle
    assert page.evaluate("() => [scrollX, scrollY]") == scroll_before  # peeking never scrolls the host page

    page.mouse.move(5, 5)
    page.wait_for_function("!document.querySelector('.shp-card')")
    page.hover('[data-ref="link-only"]')
    page.wait_for_function("document.querySelector('.shp-card')?.dataset.ref === 'link-only' && document.querySelector('.shp-card .sh-v-empty')")
    assert page.locator(".shp-card .sh-mark").count() == 0  # nothing stored: never a guessed mark

    page.click('[data-ref="html"]')
    page.wait_for_function("document.querySelector('.shp-pin-body')?.dataset.ready === '1'", timeout=20000)
    assert page.locator(".shp-card").count() == 0
    body = page.locator(".shp-pin-body").bounding_box()
    marks = page.evaluate("() => [...document.querySelectorAll('.shp-pin-body .sh-mark')].map((m) => m.getBoundingClientRect().toJSON())")
    assert len(marks) == 2
    for mark in marks:
        assert body["x"] <= mark["x"] and mark["x"] + mark["width"] <= body["x"] + body["width"]
        assert body["y"] <= mark["y"] and mark["y"] + mark["height"] <= body["y"] + body["height"]
    assert page.locator(".shp-full").get_attribute("href") == viewer + "#ref=html"
    with page.context.expect_page() as opened:
        page.click(".shp-full")
    lens = opened.value
    lens.wait_for_function("document.querySelector('.l-stage')?.dataset.ready === '1'", timeout=20000)
    assert lens.locator(".l-label").inner_text() == "Rated torque — CubeMars AK80-9"
    lens.close()
    assert page.evaluate("() => [scrollX, scrollY]") == scroll_before
    assert page.console_messages == []


def test_peek_panel_moves_and_resizes_and_is_remembered(host_site, viewer, browser_page):
    page = browser_page
    page.goto(host_site)
    wait_peek(page)
    page.click('[data-ref="pdf-box"]')
    page.wait_for_function("document.querySelector('.shp-pin-body')?.dataset.ready === '1'", timeout=20000)
    start = page.locator(".shp-pin").bounding_box()

    head = page.locator(".shp-pin-head b").bounding_box()
    page.mouse.move(head["x"] + 20, head["y"] + head["height"] / 2)
    page.mouse.down()
    page.mouse.move(head["x"] - 80, head["y"] + head["height"] / 2 + 40, steps=5)
    page.mouse.up()
    moved = page.locator(".shp-pin").bounding_box()
    assert (moved["x"] - start["x"], moved["y"] - start["y"]) == (pytest.approx(-100, abs=1), pytest.approx(40, abs=1))

    corner = page.locator(".shp-resize").bounding_box()
    page.mouse.move(corner["x"] + 9, corner["y"] + 9)
    page.mouse.down()
    page.mouse.move(corner["x"] + 9 - 90, corner["y"] + 9 + 70, steps=5)
    page.mouse.up()
    resized = page.locator(".shp-pin").bounding_box()
    assert (resized["width"] - moved["width"], resized["height"] - moved["height"]) == (pytest.approx(-90, abs=1), pytest.approx(70, abs=1))
    assert (resized["x"], resized["y"]) == (pytest.approx(moved["x"], abs=1), pytest.approx(moved["y"], abs=1))

    page.reload()
    wait_peek(page)
    page.click('[data-ref="pdf-box"]')
    page.wait_for_function("document.querySelector('.shp-pin-body')?.dataset.ready === '1'", timeout=20000)
    again = page.locator(".shp-pin").bounding_box()
    for key in ("x", "y", "width", "height"):
        assert again[key] == pytest.approx(resized[key], abs=1), key
    print(f"peek panel: {start} -> moved {moved} -> resized {resized}; after reload {again}")
    assert page.console_messages == []
