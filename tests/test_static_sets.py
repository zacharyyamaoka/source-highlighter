"""Peek and Lens from a static folder (CONTRACT §7): a reference set served by a plain static server, no view server and
no /api/state. Stored paths count from refs.json; `source-highlighter state` writes the sizes and statuses as a file.
The marks are measured against pdfplumber's character boxes, as the view-server tests measure them."""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pdfplumber
import pytest

from .conftest import CUBEMARS_HTML, FIXTURES, REPO

LRS_PDF = FIXTURES / "lrs-350.pdf"
TOLERANCE_PX = 1.0
PIN_ZOOM = 1.25  # peek.js PIN_ZOOM.pdf


def write_set(root):
    """A publishable folder: the vendored bundle, the stored copies, refs.json naming them by relative path, a host page."""
    subprocess.run([sys.executable, str(REPO / "scripts" / "vendor.py"), "--worktree", "--into", str(root / "sh")], check=True, capture_output=True)
    (root / "stored").mkdir()
    shutil.copyfile(LRS_PDF, root / "stored" / "lrs-350.pdf")
    shutil.copyfile(CUBEMARS_HTML, root / "stored" / "cubemars.html")
    refs = [
        {"id": "pdf-quote", "label": "Rated power — LRS-350-36", "source": {"url": "https://www.meanwell.com/LRS-350-SPEC.PDF",
         "retrieved_at": "2026-10-29T19:44:27Z", "stored": {"pdf": "stored/lrs-350.pdf"}}, "locations": [{"id": "rated-36", "page": 2, "quote": "349.2W"}]},
        {"id": "html", "label": "Rated torque — CubeMars AK80-9", "source": {"url": "https://www.cubemars.com/product/ak80-9.html",
         "stored": {"html": "stored/cubemars.html"}}, "locations": [], "quote": "Rated torque (Nm)"},
        {"id": "link-only", "label": "Peak torque — qdd100", "source": {"url": "https://mjbots.com/products/qdd100-beta-3", "stored": {}}, "locations": [],
         "quote": "Peak torque 100 N·m"},
    ]
    (root / "refs.json").write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
    statuses = {"checked_at": "2026-11-24T08:00:00Z", "statuses": {"pdf-quote": {"status": "live", "label": "live · unchanged since 2026-10-29",
                "why": "same sha256", "checked_at": "2026-11-24T08:00:00Z", "live_link": "https://www.meanwell.com/LRS-350-SPEC.PDF#page=2"}}}
    (root / "statuses.json").write_text(json.dumps(statuses), encoding="utf-8")
    return refs


def host_page(script_attributes: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>host</title></head><body style="font:15px/1.7 Georgia,serif;margin:40px">
<p>The LRS-350-36 is rated at <span data-ref="pdf-quote">349.2 W</span>; the AK80-9 at <span data-ref="html">9 N·m</span>;
the qdd100 peaks at <span data-ref="link-only">100 N·m</span>.</p>
<script type="module" src="sh/peek.js" data-refs="refs.json" {script_attributes}></script></body></html>"""


@pytest.fixture
def static_set(tmp_path):
    if not (REPO / "js" / "node_modules" / "pdfjs-dist").exists():
        pytest.skip("PDF.js not installed: run `pnpm --dir js install`")
    root = tmp_path / "set"
    root.mkdir()
    write_set(root)

    requests: list[str] = []

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802 - http.server's name
            requests.append(self.path)
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_address[1]}/", requests
    server.shutdown()
    server.server_close()


@pytest.fixture
def browser_page(chromium):
    context = chromium.new_context(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
    page = context.new_page()
    page.problems = []
    page.on("console", lambda message: message.type in ("error", "warning") and page.problems.append(f"{message.type}: {message.text}"))
    page.on("pageerror", lambda error: page.problems.append(f"pageerror: {error}"))
    yield page
    context.close()


def pdfplumber_rect(quote: str) -> list[float]:
    with pdfplumber.open(LRS_PDF) as pdf:
        hit = pdf.pages[1].search(quote, regex=False)[0]
    return [hit["x0"], hit["top"], hit["x1"], hit["bottom"]]


PIN_MARK = """() => { const m = document.querySelector('.shp-pin-body .sh-mark'); const o = m.closest('.sh-page').getBoundingClientRect();
    const r = m.getBoundingClientRect(); return [r.left - o.left, r.top - o.top, r.right - o.left, r.bottom - o.top]; }"""


def test_peek_and_lens_run_from_a_static_folder(static_set, browser_page):
    root, base, requests = static_set
    (root / "host.html").write_text(host_page("data-static"), encoding="utf-8")
    page = browser_page
    page.goto(base + "host.html")
    page.wait_for_function("window.sourcePeek !== undefined", timeout=20000)
    assert page.locator(".shp-cite").count() == 3

    page.hover('[data-ref="pdf-quote"]')
    page.wait_for_function("document.querySelector('.shp-card .shp-crop')?.dataset.ready === '1'", timeout=20000)
    assert page.locator(".shp-card .sh-mark").count() == 1

    page.click('[data-ref="pdf-quote"]')
    page.wait_for_function("document.querySelector('.shp-pin-body')?.dataset.ready === '1'", timeout=20000)
    x0, top, x1, bottom = pdfplumber_rect("349.2W")
    expected = [(x0 - 2) * PIN_ZOOM, (top - 2) * PIN_ZOOM, (x1 + 2) * PIN_ZOOM, (bottom + 2) * PIN_ZOOM]
    drawn = page.evaluate(PIN_MARK)
    off = max(abs(a - b) for a, b in zip(drawn, expected))
    assert off <= TOLERANCE_PX, (drawn, expected)
    # its stored link is the file itself, and "Open full" is Lens told where the set is
    links = page.evaluate("() => [document.querySelector('.shp-pin .sh-v-stored').href, document.querySelector('.shp-full').href]")
    assert links[0] == base + "stored/lrs-350.pdf"
    assert links[1] == base + "sh/lens.html?refs=" + __import__("urllib.parse").parse.quote(base + "refs.json", safe="") + "#ref=pdf-quote"

    # data-static: nothing but what the set names was asked for — no /api/state, no state.json probing
    assert not [path for path in requests if "api/state" in path or "state.json" in path or "statuses.json" in path], requests

    page.goto(links[1])
    page.wait_for_function("document.querySelector('.l-stage').dataset.ready === '1'", timeout=20000)
    assert page.locator(".l-stage .sh-mark").count() >= 1
    assert page.evaluate("() => document.querySelector('.l-head .sh-v-stored').href") == base + "stored/lrs-350.pdf"
    page.goto(base + "sh/lens.html?refs=" + __import__("urllib.parse").parse.quote(base + "refs.json", safe="") + "#ref=link-only")
    page.wait_for_function("document.querySelector('.l-stage .sh-v-empty')", timeout=20000)  # nothing stored: said, never guessed
    assert page.problems == []


def test_peek_without_a_server_falls_back_quietly(static_set, browser_page):
    """No data-static: Peek first asks for a view server, finds none, and reads the folder (and its statuses.json)."""
    root, base, requests = static_set
    (root / "host.html").write_text(host_page(""), encoding="utf-8")
    page = browser_page
    page.goto(base + "host.html")
    page.wait_for_function("window.sourcePeek !== undefined", timeout=20000)
    page.click('[data-ref="pdf-quote"]')
    page.wait_for_function("document.querySelector('.shp-pin-body')?.dataset.ready === '1'", timeout=20000)
    assert page.locator(".shp-pin-body .sh-mark").count() == 1
    assert page.evaluate("() => document.querySelector('.shp-pin .sh-v-pill').dataset.status") == "live"  # from statuses.json
    assert any("api/state" in path for path in requests)
    # the only console lines are the 404s of the optional files it asked for
    assert all("404" in line for line in page.problems), page.problems


def test_state_cli_writes_a_publishable_state(static_set, browser_page):
    root, base, requests = static_set
    out = root / "state.json"
    result = subprocess.run([sys.executable, "-m", "source_highlighter.cli", "state", str(root / "refs.json"), "--out", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    state = json.loads(out.read_text(encoding="utf-8"))
    assert state["files"]["pdf-quote"]["pdf"] == {"kept": True, "bytes": LRS_PDF.stat().st_size}
    assert state["statuses"]["pdf-quote"]["status"] == "live"  # statuses.json beside the refs, as `view` reads it
    assert str(root) not in out.read_text(encoding="utf-8") and "/home/" not in out.read_text(encoding="utf-8")  # no machine paths
    (root / "host.html").write_text(host_page('data-state="state.json"'), encoding="utf-8")
    page = browser_page
    page.goto(base + "host.html")
    page.wait_for_function("window.sourcePeek !== undefined", timeout=20000)
    page.click('[data-ref="pdf-quote"]')
    page.wait_for_function("document.querySelector('.shp-pin-body')?.dataset.ready === '1'", timeout=20000)
    assert page.evaluate("() => document.querySelector('.shp-pin .sh-v-pill').dataset.status") == "live"
    assert not any("api/state" in path for path in requests)
    assert page.problems == []


def test_the_view_server_serves_open_html_too(static_set, browser_page):
    """The same entry point under `source-highlighter view`: src is the server's /stored/<id>/<level>."""
    from source_highlighter.view import make_server

    root, _, _ = static_set
    refs = json.loads((root / "refs.json").read_text(encoding="utf-8"))
    for ref in refs:  # the view server reads stored paths relative to refs.json, as the static folder does
        ref["source"]["stored"] = {level: str(root / path) for level, path in ref["source"]["stored"].items()}
    (root / "server-refs.json").write_text(json.dumps(refs), encoding="utf-8")
    server = make_server(root / "server-refs.json")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/open.html?src=stored/pdf-quote/pdf&kind=pdf&where=page%3D2%26text%3D349.2W"
        page = browser_page
        page.goto(url)
        page.wait_for_function("['1', 'error'].includes(document.body.dataset.ready)", timeout=20000)
        assert page.evaluate("() => document.body.dataset.status") == "marked"
        assert page.locator(".sh-mark").count() == 1
        assert page.problems == []
    finally:
        server.shutdown()
        server.server_close()
