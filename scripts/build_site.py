"""Build the GitHub Pages landing page into site/: the hero video, the API section and the gallery.

    uv run python scripts/build_site.py --gim DIR

Every picture and every printed output on the page comes from running the code shown beside it, here, against
the real sources: the repo's fixtures and a SteadyWin GIM4305-10 maker page saved by
`mdify capture https://www.steadywin.cn/en/h-pd-9.html --keep html,snapshot --out DIR` (pass that DIR as --gim;
the maker's page is not committed, only the pictures made from it). The hero is recorded from the real ES module
drawing on that page in headless Chromium.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import html
import io
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

import source_highlighter
from source_highlighter import Source, TableCell

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
MEDIA = SITE / "media"
GITHUB = "https://github.com/zacharyyamaoka/source-highlighter"
GIM_URL = "https://www.steadywin.cn/en/h-pd-9.html"
GIM_TABLE = 33  # the spec table is the page's 33rd <table> (the rest are its navigation)


# --- running the snippets the page shows ---------------------------------------------------------------------------


def run_snippet(code: str, workdir: Path) -> str:
    """Execute `code` exactly as shown, in `workdir`, and return what it printed."""
    namespace: dict = {}
    output = io.StringIO()
    previous = Path.cwd()
    os.chdir(workdir)
    try:
        with contextlib.redirect_stdout(output):
            exec(compile(code, "<snippet>", "exec"), namespace)  # noqa: S102 — the page's own snippets
    finally:
        os.chdir(previous)
    return output.getvalue()


def crop_to_marks(image_path: Path, marks, out: Path, *, pad_left: int = 720, pad_right: int = 280, pad_y: int = 240,
                  max_width: int = 1100) -> tuple[int, int]:
    """WHY more room on the left: a datasheet value sits right of its parameter name, which must stay in the picture."""
    image = Image.open(image_path).convert("RGB")
    x0 = min(mark.rect.x0 for mark in marks) - pad_left
    y0 = min(mark.rect.y0 for mark in marks) - pad_y
    x1 = max(mark.rect.x1 for mark in marks) + pad_right
    y1 = max(mark.rect.y1 for mark in marks) + pad_y
    box = (max(0, int(x0)), max(0, int(y0)), min(image.width, int(x1)), min(image.height, int(y1)))
    cropped = image.crop(box)
    if cropped.width > max_width:
        cropped = cropped.resize((max_width, round(cropped.height * max_width / cropped.width)), Image.Resampling.LANCZOS)
    cropped.save(out, quality=84, optimize=True)
    return cropped.size


# --- the hero video ------------------------------------------------------------------------------------------------

HERO_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>hero</title>
<style>
html, body { margin: 0; background: #f4f3ef; font-family: system-ui, sans-serif; }
#bar { height: 64px; box-sizing: border-box; padding: 0 28px; display: flex; align-items: center; gap: 14px;
  background: #16181d; color: #e8e6df; font: 500 19px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace; }
#bar b { color: #ffd84d; font-weight: 600; }
#step { color: #8b8f99; font: 600 13px system-ui, sans-serif; letter-spacing: .06em; text-transform: uppercase; }
#surface { width: 1280px; height: 656px; overflow: auto; }
</style></head><body>
<div id="bar"><span id="step"></span><span id="code"></span></div><div id="surface"></div>
<script type="module">
import * as sh from "./source-highlighter.js";
window.sh = sh;
</script></body></html>
"""


def _serve(directory: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def record_hero(gim: Source, scratch: Path) -> None:
    """Record the ES module highlighting the GIM4305-10 page, step by step, with the Python line on screen."""
    from playwright.sync_api import sync_playwright

    serve = scratch / "hero"
    serve.mkdir()
    shutil.copy(REPO / "js" / "source-highlighter.js", serve / "source-highlighter.js")
    shutil.copytree(REPO / "js" / "vendor", serve / "vendor")
    (serve / "index.html").write_text(HERO_PAGE, encoding="utf-8")

    def cell(row: int, label: str, cell_id: str) -> dict:
        return TableCell(row, 4, table=GIM_TABLE, id=cell_id, label=label).to_location("html")

    nominal = cell(7, "Nominal torque 1 N·m", "nominal")
    stall = cell(8, "Stall torque 3.47 N·m", "stall")
    kt = cell(16, "Torque constant 0.62 N·m/A", "kt")
    resistance = cell(13, "Phase resistance 1.15 Ω", "r")
    steps = [
        ("capture", f'src = Source.from_url("{GIM_URL}").capture()', None, None, None, 2.2),
        ("highlight", 'src.highlight(<b>TableCell(7, 4, table=33)</b>, "highlight")', [nominal], "highlight", "nominal", 2.6),
        ("box", 'src.highlight([…, <b>TableCell(8, 4, table=33)</b>], "box")', [nominal, stall], "box", "stall", 2.6),
        ("spotlight", 'src.highlight([… <b>4 cells</b>], "spotlight").show()', [nominal, stall, kt, resistance], "spotlight", "kt", 3.0),
        ("blackout", 'src.highlight([… 4 cells], <b>"blackout"</b>)', [nominal, stall, kt, resistance], "blackout", "r", 2.4),
    ]
    server = _serve(serve)
    video_dir = scratch / "video"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1,
                                          record_video_dir=str(video_dir), record_video_size={"width": 1280, "height": 720})
            started = time.monotonic()
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html")
            page.wait_for_function("window.sh !== undefined")
            page.evaluate(
                """async (html) => {
                    window.handle = window.sh.highlight(document.getElementById("surface"),
                        {source: {kind: "html", html}, locations: [], style: "highlight", zoom: 1.5});
                    await window.handle.ready;
                }""",
                gim.text(),
            )
            # Frame the spec table: its left edge (the parameter names) in view, its top under the code bar.
            # WHY not focus(): focus() centres a mark both ways, which would push the names off the left edge.
            page.evaluate(
                """(tableIndex) => {
                    const surface = document.getElementById("surface");
                    const frame = surface.querySelector("iframe");
                    const zoom = window.handle.zoom;
                    const table = frame.contentDocument.querySelectorAll("table")[tableIndex - 1].getBoundingClientRect();
                    window.frameLeft = Math.max(0, table.left * zoom - (surface.clientWidth - table.width * zoom) / 2);
                    surface.scrollTo({left: window.frameLeft, top: table.top * zoom - 24});
                }""",
                GIM_TABLE,
            )
            page.wait_for_timeout(400)
            ready_at = time.monotonic() - started
            for step, code, locations, style, focus, seconds in steps:
                page.evaluate(
                    """async ({step, code, locations, style, focus}) => {
                        document.getElementById("step").textContent = step;
                        document.getElementById("code").innerHTML = code;
                        if (!locations) return;
                        await window.handle.update(locations, {name: style, label: true});
                        const surface = document.getElementById("surface");
                        const mark = surface.querySelector(`.sh-mark[data-id="${focus}"]`);
                        const top = mark.getBoundingClientRect().top - surface.getBoundingClientRect().top + surface.scrollTop;
                        surface.scrollTo({left: window.frameLeft, top: Math.max(0, top - 260), behavior: "smooth"});
                    }""",
                    {"step": step, "code": code, "locations": locations, "style": style, "focus": focus},
                )
                page.wait_for_timeout(int(seconds * 1000))
            video = page.video.path()
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    raw = Path(video)
    common = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ready_at:.2f}", "-i", str(raw)]
    subprocess.run([*common, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28", "-preset", "slow",
                    "-movflags", "+faststart", str(MEDIA / "hero.mp4")], check=True)
    subprocess.run([*common, "-an", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "40", str(MEDIA / "hero.webm")], check=True)
    subprocess.run([*common, "-vf", "fps=8,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse",
                    str(MEDIA / "hero.gif")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-3.6", "-i", str(raw), "-frames:v", "1", "-q:v", "4",
                    str(MEDIA / "hero-poster.jpg")], check=True)


# --- the gallery ---------------------------------------------------------------------------------------------------


def gallery(work: Path, gim_dir: Path) -> list[dict]:
    """One card per source kind: the 3 lines, run as shown, and the picture they wrote."""
    words = json.loads((gim_dir / "snapshot.json").read_text(encoding="utf-8"))["words"]
    stall_box = next(word for word in words if word[0] == "3.47")[3:7]
    x0, y0, x1, y1 = stall_box
    pad_x, pad_y = 0.004, 0.0025
    image_box = f"PageBox(1, {x0 - pad_x:.4f}, {y0 - pad_y:.4f}, {x1 - x0 + 2 * pad_x:.4f}, {y1 - y0 + 2 * pad_y:.4f})"
    j60 = {loc["id"]: loc for loc in json.loads((REPO / "tests/fixtures/j60-joint.locations.json").read_text())}
    boxes = ", ".join(
        f'"{source_highlighter.from_location(j60[key]).compact().split("&id=")[0]}"'
        for key in ("peak-torque-label", "peak-torque-j60-6", "peak-torque-j60-10")
    )
    cards = [
        {
            "kind": "PDF",
            "file": "pdf",
            "what": "A datasheet PDF, page 2 of the J60 joint: the peak-torque row, spotlit. Geometry-anchored boxes "
                    "(compact strings: PDF page + Media Fragments xywh), drawn into the page by pdfium.",
            "code": f'src = Source.from_file("j60-joint.pdf")\n'
                    f"hl = src.highlight([{boxes}], \"spotlight\")\n"
                    f'hl.save("pdf.png")',
        },
        {
            "kind": "HTML page",
            "file": "html",
            "what": "CubeMars' AK80-9 product page, archived (scripts off, no network). A text quote pinned by its "
                    "prefix — found exactly once in the page, else reported.",
            "code": 'src = Source.from_file("cubemars-ak80-9.html")\n'
                    'hl = src.highlight(TextQuote("22", prefix="Peak torque (Nm)", label="peak"), "box", label=True)\n'
                    'hl.save("html.png")',
        },
        {
            "kind": "Markdown",
            "file": "markdown",
            "what": "The same spec as a markdown table. TableCell counts rows from the header row; it becomes the "
                    "cell's exact character span.",
            "code": 'src = Source.from_file("cubemars-ak80-9-spec.md")\n'
                    'hl = src.highlight([TableCell(19, 1), TableCell(19, 2)], "highlight")\n'
                    'hl.save("markdown.png")',
        },
        {
            "kind": "Image / screenshot",
            "file": "image",
            "what": "The GIM4305-10 page as a picture (mdify's capture-time snapshot). No text layer, so the "
                    "location is a box: the word box mdify measured for “3.47”.",
            "code": 'src = Source.from_file("steadywin-gim4305/snapshot.jpg")\n'
                    f'hl = src.highlight({image_box}, "box")\n'
                    'hl.save("image.png")',
        },
        {
            "kind": "Table cell",
            "file": "table-cell",
            "what": "The live GIM4305-10 page, saved by mdify capture: the GDS34 column's stall torque, by its cell "
                    "— not by searching for “3.47”, so a value that repeats can't fool it.",
            "code": 'src = Source.from_bundle("steadywin-gim4305")  # mdify capture ' + GIM_URL + "\n"
                    'hl = src.highlight(TableCell(8, 4, table=33, label="stall torque, GDS34"), "spotlight", label=True)\n'
                    'hl.save("table-cell.png")',
        },
    ]
    for card in cards:
        namespace_code = "from source_highlighter import *\n" + card["code"].replace("\nhl.save(", "\n_result = hl.save(")
        namespace: dict = {}
        previous = Path.cwd()
        os.chdir(work)
        try:
            exec(compile(namespace_code, "<card>", "exec"), namespace)  # noqa: S102
        finally:
            os.chdir(previous)
        result = namespace["_result"]
        if result.unresolved or not result.marks:
            raise SystemExit(f"{card['kind']}: drew {len(result.marks)} marks, unresolved {result.unresolved}")
        output = Path(work) / result.outputs[0]
        card["size"] = crop_to_marks(output, result.marks, MEDIA / f"gallery-{card['file']}.jpg")
        card["marks"] = len(result.marks)
    return cards


# --- the API section: snippets and their real output ----------------------------------------------------------------

API_SNIPPETS = {
    "source": '''\
from source_highlighter import Source

link = Source.from_url("https://www.steadywin.cn/en/h-pd-9.html")   # a link: no network yet
print(link.kind, link.media_type, link.is_saved)
pdf = Source.from_file("j60-joint.pdf")                              # read, hashed, kind sniffed
print(pdf.kind, pdf.media_type, pdf.id[:12], pdf.retrieved_at[:4])
note = Source.from_text("| Part | Rated torque |\\n|---|---|\\n| GIM4305-10 | 1 N·m |\\n")
print(note.kind, note.media_type, note.to_markdown().splitlines()[2])
print(sorted(pdf.to_dict()))                                         # a CONTRACT §5 Reference source''',
    "verbs": '''\
from source_highlighter import Source, TableCell

src = Source.from_bundle("steadywin-gim4305")            # = Source.from_url(url).capture(), saved earlier
hl = src.highlight(TableCell(8, 4, table=33), "spotlight")
print(hl)
result = hl.save("stall.png")                            # always headless
print(len(result.marks), "mark,", len(result.unresolved), "unresolved")
page = hl.show()                                         # browser / notebook; headless here: opens nothing
print(page.name)''',
    "selectors": '''\
from source_highlighter import *

for selector, kind in [
    (TextQuote("20 Nm", prefix="Rated torque ", suffix=" Peak"), "html"),
    (TextPosition(840, 846), "text"),
    (PageBox(2, 0.61, 0.40, 0.07, 0.02), "pdf"),
    (Page(33), "pdf"),
    (XPath("/html/body/main/table[2]/tr[3]/td[2]"), "html"),
    (Css("table.spec td.value"), "html"),
    (TableCell(3, 2), "html"),
    (Note(heading="Pricing"), "text"),
]:
    print(f"{type(selector).__name__:12} {selector.anchor:8} {selector.compact()}")
    print(f"{'':21} → {selector.to_location(kind)}")''',
    "unresolved": '''\
from source_highlighter import Source, TableCell, PageBox

src = Source.from_file("j60-joint.pdf")
result = src.highlight([TableCell(3, 2), "page=2&xywh=percent:19,30.9,6.4,0.8"], "box").save("mixed.png")
print(len(result.marks), "drawn")
for entry in result.unresolved:
    print(entry.location, "→", entry.reason)''',
}


def code_block(code: str) -> str:
    return f'<pre class="code"><code>{html.escape(code)}</code></pre>'


def output_block(output: str) -> str:
    return f'<pre class="out"><code>{html.escape(output.rstrip())}</code></pre>'


# --- the page --------------------------------------------------------------------------------------------------------


def build_page(api: dict[str, str], cards: list[dict], hero: bool) -> str:
    source_fields = [
        ("origin", "str", "where it came from: a URL, or a file's absolute path"),
        ("kind", "str", "<code>pdf</code> · <code>html</code> · <code>image</code> · <code>text</code> — what gets drawn on"),
        ("media_type", "str", "<code>application/pdf</code>, <code>text/html</code>, <code>text/markdown</code>, …"),
        ("sha256", "str | None", "of the saved bytes; <code>None</code> while it is only a link"),
        ("path", "str | None", "the saved bytes (a file is saved as itself)"),
        ("retrieved_at", "str | None", "when, ISO 8601 UTC"),
        ("stored", "FrozenDict", "other capture levels kept: snapshot, pdf, single, archive"),
        ("title", "str | None", "a human name"),
    ]
    fields_rows = "".join(f"<tr><td><code>{name}</code></td><td><code>{kind}</code></td><td>{what}</td></tr>"
                          for name, kind, what in source_fields)
    selector_rows = [
        ("TextQuote", "text", "exact, prefix, suffix, page", "text=prefix-,exact,-suffix", "Text Fragments"),
        ("TextPosition", "text", "start, end", "char=840,846", "RFC 5147"),
        ("XPath", "text", "path", "xpath=/html/body/…", "W3C XPathSelector"),
        ("Css", "text", "selector", "css=table.spec td", "W3C CssSelector"),
        ("TableCell", "text", "row, col, table", "table=1&row=3&col=2", "—"),
        ("Note", "text", "heading, block", "heading=Pricing", "Obsidian links"),
        ("PageBox", "geometry", "page, x, y, w, h (0–1)", "page=2&xywh=percent:61,40,7,2", "Media Fragments + PDF open params"),
        ("Page", "geometry", "page", "page=33", "PDF open parameters"),
    ]
    selectors_table = "".join(
        f"<tr><td><code>{name}</code></td><td><span class='tag {anchor}'>{anchor}</span></td><td>{fields}</td>"
        f"<td><code>{html.escape(compact)}</code></td><td>{borrowed}</td></tr>"
        for name, anchor, fields, compact, borrowed in selector_rows
    )
    gallery_cards = "".join(
        f"""<figure class="card"><a href="media/gallery-{card['file']}.jpg"><img src="media/gallery-{card['file']}.jpg"
 width="{card['size'][0]}" height="{card['size'][1]}" alt="{html.escape(card['kind'])} source, highlighted"></a>
<figcaption><h3>{html.escape(card['kind'])}</h3><p>{html.escape(card['what'])}</p>{code_block(card['code'])}</figcaption></figure>"""
        for card in cards
    )
    hero_media = (
        """<video autoplay muted loop playsinline controls poster="media/hero-poster.jpg" width="1280" height="720">
<source src="media/hero.webm" type="video/webm"><source src="media/hero.mp4" type="video/mp4">
<img src="media/hero.gif" alt="The highlighter marking four cells of the GIM4305-10 spec table" width="640" height="360"></video>"""
        if hero else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Source Highlighter</title>
<meta name="description" content="Source + locations + style in → the source, highlighted, out. PDF, HTML, markdown and images.">
<style>
:root {{ --bg:#f6f5f1; --panel:#ffffff; --ink:#1c1d21; --muted:#5d616b; --line:#dedbd2; --accent:#e5322d; --hl:#ffe600;
  --code-bg:#16181d; --code-ink:#e8e6df; --out-bg:#eeece6; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#121317; --panel:#1b1d22; --ink:#ecebe6;
  --muted:#a2a5ad; --line:#2d3038; --out-bg:#202329; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1120px; margin:0 auto; padding:0 16px 80px; }}
header {{ padding:56px 0 24px; }}
h1 {{ font-size:clamp(30px,5vw,48px); line-height:1.1; margin:0 0 14px; letter-spacing:-.02em; }}
.contract {{ font-size:clamp(18px,2.6vw,24px); margin:0 0 18px; }}
.contract mark {{ background:linear-gradient(transparent 55%, color-mix(in srgb, var(--hl) 70%, transparent) 55%); color:inherit; padding:0 2px; }}
.lede {{ color:var(--muted); max-width:760px; margin:0 0 20px; }}
.links a {{ color:var(--ink); margin-right:18px; font-weight:600; }}
video, .hero img {{ width:100%; height:auto; border-radius:10px; box-shadow:0 6px 30px #0003; background:#000; display:block; }}
.hero p {{ color:var(--muted); font-size:14px; margin:8px 0 0; }}
section {{ margin-top:64px; }}
h2 {{ font-size:28px; margin:0 0 6px; letter-spacing:-.01em; }}
h3 {{ margin:28px 0 6px; font-size:19px; }}
.sub {{ color:var(--muted); margin:0 0 18px; }}
pre {{ margin:0; overflow-x:auto; border-radius:8px; padding:14px 16px; font:13.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
pre.code {{ background:var(--code-bg); color:var(--code-ink); }}
pre.out {{ background:var(--out-bg); color:var(--ink); border:1px solid var(--line); margin-top:6px; }}
pre.out::before {{ content:"output"; display:block; font:600 11px system-ui; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin-bottom:4px; }}
.pair {{ display:grid; gap:6px; }}
table {{ border-collapse:collapse; width:100%; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; font-size:14.5px; }}
th, td {{ text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }}
.scroll {{ overflow-x:auto; }}
.tag {{ font:600 12px system-ui; padding:1px 8px; border-radius:99px; }}
.tag.text {{ background:color-mix(in srgb, var(--hl) 55%, transparent); color:#1c1d21; }}
.tag.geometry {{ background:color-mix(in srgb, var(--accent) 22%, transparent); }}
.big {{ font:600 clamp(16px,2.4vw,21px)/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; background:var(--panel); border:1px solid var(--line);
  border-radius:10px; padding:18px 20px; overflow-x:auto; }}
.big b {{ color:var(--accent); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr)); gap:22px; }}
.card {{ margin:0; background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }}
.card > a {{ display:block; background:#fff; border-bottom:1px solid var(--line); max-height:420px; overflow:hidden; }}
.card img {{ width:100%; height:auto; display:block; }}
.card figcaption {{ padding:14px 16px 16px; }}
.card h3 {{ margin:0 0 4px; }}
.card p {{ margin:0 0 10px; color:var(--muted); font-size:14.5px; }}
.card pre {{ font-size:12.5px; white-space:pre-wrap; overflow-wrap:anywhere; }}
footer {{ margin-top:72px; color:var(--muted); font-size:14px; border-top:1px solid var(--line); padding-top:18px; }}
code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.92em; }}
</style></head>
<body><main>
<header>
<h1>Source Highlighter</h1>
<p class="contract"><mark>source + locations + style in → the source, highlighted, out.</mark></p>
<p class="lede">Give it a PDF, a web page, a markdown note or an image, say where (a quote, a span, a table cell, a box on a
page) and how (highlight, box, underline, spotlight, blackout). You get the source back with those places marked — as a
picture, a marked PDF, or live in a browser. It never decides <em>what</em> to mark; that's the caller's job.</p>
<p class="links"><a href="{GITHUB}">GitHub</a><a href="{GITHUB}/blob/main/docs/peps/0001-the-source-type.md">PEP 0001: the Source type</a><a href="{GITHUB}/blob/main/CONTRACT.md">CONTRACT</a></p>
</header>

<div class="hero">{hero_media}
<p>Recorded from the real ES module in headless Chromium, on SteadyWin's GIM4305-10 page saved by <code>mdify capture</code>
({GIM_URL}). Each step's Python is in the bar; the cells are found by their place in the table, not by their text.</p></div>

<section id="api"><h2>The API</h2>
<p class="sub">One type in the middle. Make a <code>Source</code> however you like; every way gives the same thing.
Then highlight it, and show or save it — like matplotlib's <code>show()</code> and <code>savefig()</code>.</p>
<div class="big">Source.from_url(url)<b>.capture()</b><b>.highlight(</b>locations, style<b>).show()</b></div>

<h3>1 · <code>Source</code> — one frozen type, set fields</h3>
<p class="sub">A frozen dataclass in <code>source_msgs</code>, a stdlib-only package every source app can import without
importing the others (re-exported by <code>source_highlighter</code>). Its JSON is the <code>source</code> a stored
reference already carries.</p>
<div class="scroll"><table><tr><th>field</th><th>type</th><th>holds</th></tr>{fields_rows}</table></div>
<h3>Constructors: many ways in, one type out</h3>
<div class="pair">{code_block(API_SNIPPETS['source'])}{output_block(api['source'])}</div>

<h3>2 · The verbs</h3>
<div class="scroll"><table><tr><th>verb</th><th>does</th></tr>
<tr><td><code>.capture(keep=("html","snapshot"))</code></td><td>saves the bytes: through <code>mdify capture</code> when it is installed (every capture level, word boxes on the snapshot), else one polite request (robots.txt honoured). A no-op once saved.</td></tr>
<tr><td><code>.to_markdown()</code></td><td>markdown of it: read as is for markdown, through <code>mdify</code> for a PDF, page or image.</td></tr>
<tr><td><code>.highlight(locations, style)</code></td><td>→ <code>Highlighted</code>: the source + locations + style, as data. Nothing drawn yet.</td></tr>
<tr><td><code>Highlighted.show()</code></td><td>draws it and opens it in your browser (never raised over your window) or inline in a notebook. <code>SOURCE_HIGHLIGHTER_BACKEND=none</code>, no display, or pytest: opens nothing.</td></tr>
<tr><td><code>Highlighted.save(path)</code></td><td>draws it into a PNG / JPEG / marked PDF / HTML page; returns the marks and anything it could not place, with why.</td></tr>
</table></div>
<div class="pair" style="margin-top:12px">{code_block(API_SNIPPETS['verbs'])}{output_block(api['verbs'])}</div>

<h3>3 · Locations: a small union of selectors</h3>
<p class="sub">Two families. <b>Text-anchored</b> selectors point into a source's underlying text — HTML, markdown, a
PDF's text layer. <b>Geometry-anchored</b> ones point at a rendered surface with no text — a photo, a scan, a
screenshot. Each has a one-line compact form borrowed from a web standard, and turns into the JSON the highlighter
draws for whichever kind of source it meets. Finding "this text" in a source is a resolver step below the
highlighter (the quote find, mdify's index); the selectors carry its answer.</p>
<div class="scroll"><table><tr><th>selector</th><th>anchor</th><th>fields</th><th>compact</th><th>borrowed from</th></tr>{selectors_table}</table></div>
<div class="pair" style="margin-top:12px">{code_block(API_SNIPPETS['selectors'])}{output_block(api['selectors'])}</div>
<h3>Nothing is dropped silently</h3>
<p class="sub">A selector that can't address this kind of source is reported with the reason, beside the marks that were drawn.</p>
<div class="pair">{code_block(API_SNIPPETS['unresolved'])}{output_block(api['unresolved'])}</div>
</section>

<section id="gallery"><h2>Gallery</h2>
<p class="sub">One card per kind of source. Each picture is the real output of the three lines under it (cropped around the marks).</p>
<div class="grid">{gallery_cards}</div></section>

<footer>
<p><b>Install</b> (Python ≥ 3.12; HTML and text also need <code>playwright install chromium</code>):</p>
{code_block('pip install "source-highlighter @ git+' + GITHUB + '"')}
<p>The interactive form is one framework-free ES module, <code>js/source-highlighter.js</code>. MIT licensed.
Datasheet pages shown belong to their makers (SteadyWin, CubeMars, and the J60 joint's maker); they appear here as the sources being cited.
This page is built by <code>scripts/build_site.py</code>; every output above was printed by the code beside it.</p>
</footer>
</main></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gim", required=True, help=f"a folder written by `mdify capture {GIM_URL} --keep html,snapshot --out DIR`")
    parser.add_argument("--no-hero", action="store_true", help="skip recording the hero video (keeps the old one)")
    args = parser.parse_args()
    gim_dir = Path(args.gim).resolve()
    MEDIA.mkdir(parents=True, exist_ok=True)
    os.environ["SOURCE_HIGHLIGHTER_BACKEND"] = "none"
    with tempfile.TemporaryDirectory(prefix="sh-site-") as scratch_name:
        scratch = Path(scratch_name)
        os.environ["SOURCE_MSGS_CACHE"] = str(scratch / "cache")
        os.environ["SOURCE_HIGHLIGHTER_SHOW_DIR"] = str(scratch / "shown")
        work = scratch / "work"
        work.mkdir()
        for name in ("j60-joint.pdf", "cubemars-ak80-9.html", "cubemars-ak80-9-spec.md"):
            shutil.copy(REPO / "tests" / "fixtures" / name, work / name)
        shutil.copytree(gim_dir, work / "steadywin-gim4305",
                        ignore=shutil.ignore_patterns("bundle", "fragbundle", "frag.html"))
        api = {name: run_snippet(textwrap.dedent(code), work) for name, code in API_SNIPPETS.items()}
        cards = gallery(work, gim_dir)
        hero = (MEDIA / "hero.mp4").exists()
        if not args.no_hero:
            record_hero(Source.from_bundle(work / "steadywin-gim4305"), scratch)
            hero = True
    (SITE / "index.html").write_text(build_page(api, cards, hero), encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    sizes = {path.name: path.stat().st_size for path in sorted(MEDIA.iterdir())}
    print(json.dumps({"site": str(SITE), "media_bytes": sizes, "total_bytes": sum(sizes.values())}, indent=1))


if __name__ == "__main__":
    main()
