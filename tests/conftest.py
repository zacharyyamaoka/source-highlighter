"""Shared fixtures: real sources, a local static server for the JS module, one headless Chromium."""

from __future__ import annotations

import functools
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
SNAP = Path(
    os.environ.get(
        "SOURCE_HIGHLIGHTER_SNAP",
        "/tmp/claude-1000/-home-bam-zach-brain--claude-worktrees-datasheet-ingestion-parser-e51022/"
        "af9ade88-bf96-4801-9b31-49dc0f495b6a/scratchpad/snap",
    )
)
J60_PDF = FIXTURES / "j60-joint.pdf"
CUBEMARS_HTML = FIXTURES / "cubemars-ak80-9.html"
CUBEMARS_MD = FIXTURES / "cubemars-ak80-9-spec.md"
# The two page screenshots are 1.2–1.4 MB each — too big to commit, so their tests skip when they are absent.
EROB_JPG = SNAP / "eRob80.html.jpg"
CUBEMARS_TWIN_JPG = SNAP / "ak80-9-v3-0-robotic-actuator.html.jpg"


def load_locations(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def requires(path: Path):
    return pytest.mark.skipif(not path.exists(), reason=f"test source not present: {path}")


def pixels(path_or_image) -> np.ndarray:
    image = path_or_image if isinstance(path_or_image, Image.Image) else Image.open(path_or_image)
    return np.asarray(image.convert("RGB")).astype(np.int16)


def inside_mask(shape, rects, *, grow: float = 0.0) -> np.ndarray:
    """Pixels lying *fully* inside any rect (grown by `grow` px) — independent of how a renderer rounds edges."""
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=bool)
    for x0, y0, x1, y1 in rects:
        left, top = int(np.ceil(x0 - grow)), int(np.ceil(y0 - grow))
        right, bottom = int(np.floor(x1 + grow)), int(np.floor(y1 + grow))
        mask[max(0, top) : max(0, bottom), max(0, left) : max(0, right)] = True
    return mask


def touching_mask(shape, rects, *, grow: float = 0.0) -> np.ndarray:
    """Pixels that touch any rect (grown by `grow` px) at all; its complement is 'fully outside'."""
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=bool)
    for x0, y0, x1, y1 in rects:
        left, top = int(np.floor(x0 - grow)), int(np.floor(y0 - grow))
        right, bottom = int(np.ceil(x1 + grow)), int(np.ceil(y1 + grow))
        mask[max(0, top) : max(0, bottom), max(0, left) : max(0, right)] = True
    return mask


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def static_server():
    """Serve the repo root on an ephemeral port (the harness imports /js/… and /tests/fixtures/…)."""
    if not (REPO / "js" / "node_modules" / "pdfjs-dist").exists():
        pytest.skip("PDF.js not installed: run `pnpm --dir js install`")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(REPO)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def harness(chromium, static_server):
    """A fresh page with the ES module loaded (window.sourceHighlighter)."""
    context = chromium.new_context(viewport={"width": 1200, "height": 900}, device_scale_factor=1)
    page = context.new_page()
    errors: list[str] = []
    console: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: console.append(f"{message.type}: {message.text}"))
    page.goto(f"{static_server}/tests/harness.html")
    page.wait_for_function("window.sourceHighlighter !== undefined")
    page.errors = errors
    page.console = console
    yield page
    context.close()


DRAW_JS = """async ({source, locations, style, zoom, width, containerHeight, page}) => {
    const surface = document.getElementById("surface");
    surface.replaceChildren();
    if (containerHeight) Object.assign(surface.style, {height: containerHeight + "px", overflow: "auto"});
    const handle = window.sourceHighlighter.highlight(surface, {
        source, locations, style, zoom, width, page, pdfjs: window.PDFJS_URL,
    });
    window.handle = handle;
    const result = await handle.ready;
    const pages = [...surface.querySelectorAll(".sh-page")];
    const drawn = [...surface.querySelectorAll(".sh-mark")].map((mark) => {
        const pageElement = mark.closest(".sh-page");
        const origin = pageElement.getBoundingClientRect();
        const rect = mark.getBoundingClientRect();
        return {id: mark.dataset.id, page: Number(pageElement.dataset.page),
                rect: [rect.left - origin.left, rect.top - origin.top, rect.right - origin.left, rect.bottom - origin.top]};
    });
    const pageSizes = pages.map((p) => { const r = p.getBoundingClientRect(); return [r.width, r.height]; });
    return {result, drawn, pageSizes, zoom: handle.zoom};
}"""


def draw(page, source: dict, locations, style, *, zoom=1, width=None, container_height=None, page_number=None) -> dict:
    """Run highlight() in the harness and return the drawn marks' rects, relative to their page element."""
    return page.evaluate(
        DRAW_JS,
        {
            "source": source,
            "locations": locations,
            "style": style,
            "zoom": zoom,
            "width": width,
            "containerHeight": container_height,
            "page": page_number,
        },
    )
