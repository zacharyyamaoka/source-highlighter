"""HTML and text sources, rendered by headless Chromium (Playwright) running the interactive module itself.

WHY reuse the JS module for the static form: an HTML range only has a position once a browser has laid the page
out, so the static PNG and the interactive overlay draw from one implementation and cannot disagree.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path
from typing import Any

from .geometry import Layer

# A made-up origin the route handler serves from memory; nothing ever reaches the network.
HARNESS_ORIGIN = "https://source-highlighter.invalid/"


def js_file(name: str = "source-highlighter.js") -> str:
    """A file of the ES module (`source-highlighter.js`, `vendor/…`): the copy inside an installed wheel, else js/
    in a checkout."""
    packaged = resources.files("source_highlighter").joinpath("js", name)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    checkout = Path(__file__).resolve().parents[2] / "js" / name
    return checkout.read_text(encoding="utf-8")


def js_module_source() -> str:
    """The ES module itself."""
    return js_file("source-highlighter.js")


def js_folder_files(folder_name: str) -> dict[str, bytes]:
    """Every file of a folder beside the module (`like-obsidian`: the vendored markdown viewer), by name."""
    packaged = resources.files("source_highlighter").joinpath("js", folder_name)
    folder = packaged if packaged.is_dir() else Path(__file__).resolve().parents[2] / "js" / folder_name
    return {entry.name: entry.read_bytes() for entry in folder.iterdir() if entry.is_file()}


CONTENT_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json"}


def js_vendor_names() -> list[str]:
    """The vendored modules the ES module loads on demand (vendor/*.js)."""
    packaged = resources.files("source_highlighter").joinpath("js", "vendor")
    folder = packaged if packaged.is_dir() else Path(__file__).resolve().parents[2] / "js" / "vendor"
    return sorted(entry.name for entry in folder.iterdir() if entry.name.endswith(".js"))


def _harness_html(width: int) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>html, body {{ margin: 0; background: #fff; }} #surface {{ width: {width}px; }}
.sh-root {{ gap: 0 !important; }} .sh-page {{ box-shadow: none !important; }}</style></head>
<body><div id="surface"></div>
<script type="module">import * as sourceHighlighter from "./source-highlighter.js"; window.sourceHighlighter = sourceHighlighter;</script>
</body></html>"""


def _layers_for_js(layers: list[Layer]) -> list[dict[str, Any]]:
    return [{"locations": layer.locations, "style": layer.style.as_dict()} for layer in layers]


def render_in_browser(*args, **kwargs) -> dict[str, Any]:
    """Run the Chromium render on its own thread.

    WHY: Playwright's sync API refuses to start inside a running asyncio loop (a web server, a notebook, a test
    that already drives a browser). A fresh thread has no loop, so render() works from any caller.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_render_in_browser, *args, **kwargs).result()


def _render_in_browser(
    kind: str,
    source_text: str,
    layers: list[Layer],
    *,
    width: int,
    scale: float,
    out_png: Path,
) -> dict[str, Any]:
    """Lay out `source_text` (kind "html", "text" or "markdown" — rendered) at `width` CSS px, draw the marks,
    screenshot the page.

    Returns the module's {marks, unresolved}, with mark rects in source units (CSS px at zoom 1).
    """
    from playwright.sync_api import sync_playwright

    module_source = js_module_source()
    vendor = {HARNESS_ORIGIN + "vendor/" + name: js_file("vendor/" + name) for name in js_vendor_names()}
    viewer = {HARNESS_ORIGIN + "like-obsidian/" + name: body for name, body in js_folder_files("like-obsidian").items()} if kind == "markdown" else {}

    def serve(route):
        url = route.request.url
        if url == HARNESS_ORIGIN:
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=_harness_html(width))
        elif url == HARNESS_ORIGIN + "source-highlighter.js":
            route.fulfill(status=200, content_type="text/javascript; charset=utf-8", body=module_source)
        elif url in vendor:
            route.fulfill(status=200, content_type="text/javascript; charset=utf-8", body=vendor[url])
        elif url in viewer:
            route.fulfill(status=200, content_type=CONTENT_TYPES.get(Path(url).suffix, "application/octet-stream"), body=viewer[url])
        else:
            # Archived pages reference their live CSS, fonts and trackers; none of it is fetched.
            route.abort()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=scale)
            page.route("**/*", serve)
            page.goto(HARNESS_ORIGIN)
            page.wait_for_function("window.sourceHighlighter !== undefined")
            source_key = "html" if kind == "html" else "text"
            result = page.evaluate(
                """async ({kind, sourceKey, sourceText, layers, width}) => {
                    const handle = window.sourceHighlighter.highlight(document.getElementById("surface"), {
                        source: {kind, [sourceKey]: sourceText}, layers, zoom: 1, width,
                    });
                    return await handle.ready;
                }""",
                {"kind": kind, "sourceKey": source_key, "sourceText": source_text, "layers": _layers_for_js(layers), "width": width},
            )
            page.locator(".sh-page").first.screenshot(path=str(out_png), animations="disabled")
        finally:
            browser.close()
    return result


def resolve_in_browser(jobs: list[tuple[str, str, list[dict]]], *, width: int = 1280) -> list[dict[str, Any]]:
    """Resolve each job's Locations on its source with the module, in one headless Chromium, without a screenshot.

    jobs: (kind "html" | "text", source text, Locations). Returns each job's {marks, unresolved} in order.
    WHY the module and not a Python resolver: CONTRACT §2's html rules (the parser's own source_range mapping, the
    tbody retry, the ambiguity rule) and the quote find live there once; the §6 check asks the same question.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_resolve_in_browser, jobs, width).result()


def _resolve_in_browser(jobs, width: int) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    module_source = js_module_source()
    vendor = {HARNESS_ORIGIN + "vendor/" + name: js_file("vendor/" + name) for name in js_vendor_names()}

    def serve(route):
        url = route.request.url
        if url == HARNESS_ORIGIN:
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=_harness_html(width))
        elif url == HARNESS_ORIGIN + "source-highlighter.js":
            route.fulfill(status=200, content_type="text/javascript; charset=utf-8", body=module_source)
        elif url in vendor:
            route.fulfill(status=200, content_type="text/javascript; charset=utf-8", body=vendor[url])
        else:
            route.abort()

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.route("**/*", serve)
            page.goto(HARNESS_ORIGIN)
            page.wait_for_function("window.sourceHighlighter !== undefined")
            for kind, source_text, locations in jobs:
                results.append(
                    page.evaluate(
                        """async ({kind, sourceText, locations, width}) => {
                            const surface = document.getElementById("surface");
                            surface.replaceChildren();
                            const handle = window.sourceHighlighter.highlight(surface, {
                                source: {kind, [kind === "html" ? "html" : "text"]: sourceText}, locations, style: "box", zoom: 1, width,
                            });
                            const result = await handle.ready;
                            handle.destroy();
                            return result;
                        }""",
                        {"kind": kind, "sourceText": source_text, "locations": locations, "width": width},
                    )
                )
        finally:
            browser.close()
    return results


def write_text_page(source_text: str, layers: list[Layer], *, width: int, out_html: Path) -> None:
    """A self-contained HTML page (module inlined) showing the text with its ranges marked."""
    payload = json.dumps({"text": source_text, "layers": _layers_for_js(layers), "width": width})
    # WHY: "</" inside an inline <script> could close it early.
    payload = payload.replace("</", "<\\/")
    module_source = js_module_source().replace("</script", "<\\/script")
    out_html.write_text(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Highlighted text</title>
<style>body {{ margin: 0; padding: 24px; background: #f3f4f6; }}</style></head>
<body><div id="surface"></div>
<script type="module">
{module_source}
const data = {payload};
highlight(document.getElementById("surface"), {{source: {{kind: "text", text: data.text}}, layers: data.layers, zoom: 1, width: data.width}});
</script></body></html>
""",
        encoding="utf-8",
    )
