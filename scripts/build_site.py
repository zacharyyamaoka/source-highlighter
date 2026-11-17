"""Build the GitHub Pages landing page into site/: the hero video, the API section, the live gallery and the tests.

    uv run python scripts/build_site.py --gim DIR [--no-hero]
    uv run python scripts/build_site.py --stage        # only site/lib/ (what CI does before deploying)

The gallery is the drop-in viewer itself, running in the page on the repo's fixtures (staged into the gitignored
site/lib/). The tests section reads site/tests.json, which only the Pages workflow writes, from the CI run's JUnit XML
(scripts/ci_tests_json.py); a local build shows "no CI run".

Every picture and every printed output on the page comes from running the code shown beside it, here, against
the real sources: the repo's fixtures and a SteadyWin GIM4305-10 maker page saved by
`markdownify capture https://www.steadywin.cn/en/h-pd-9.html --keep html,snapshot --out DIR` (pass that DIR as --gim;
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

import source_highlighter
from source_highlighter import Source, TableCell, TextQuote

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


# --- the gallery: every card is the live drop-in viewer -------------------------------------------------------------

FIXTURES = REPO / "tests" / "fixtures"
LIB = SITE / "lib"  # staged, not committed: the module, its vendored parts, PDF.js and the gallery's sources


def stage_live_assets() -> None:
    """Copy what the live gallery loads into site/lib/ (gitignored; the Pages workflow stages it from the checkout)."""
    if LIB.exists():
        shutil.rmtree(LIB)
    (LIB / "fixtures").mkdir(parents=True)
    shutil.copy(REPO / "js" / "source-highlighter.js", LIB / "source-highlighter.js")
    shutil.copytree(REPO / "js" / "vendor", LIB / "vendor")
    shutil.copytree(REPO / "js" / "like-obsidian", LIB / "like-obsidian")
    pdfjs = REPO / "js" / "node_modules" / "pdfjs-dist" / "build"
    (LIB / "pdfjs").mkdir()
    for name in ("pdf.min.mjs", "pdf.worker.min.mjs"):
        shutil.copy(pdfjs / name, LIB / "pdfjs" / name)
    for name in ("j60-joint.pdf", "cubemars-ak80-9.html", "cubemars-ak80-9-spec.md", "erob80-band.jpg"):
        shutil.copy(FIXTURES / name, LIB / "fixtures" / name)


def live_cards() -> list[dict]:
    """One card per kind of source. `locations` are what the page's viewer is given, verbatim; `python` is the
    static form of the same marks, run here (with the default style) so a card can't show a call that fails."""
    markdown = (FIXTURES / "cubemars-ak80-9-spec.md").read_text(encoding="utf-8")
    j60 = json.loads((FIXTURES / "j60-joint.locations.json").read_text(encoding="utf-8"))
    j60 = [loc for loc in j60 if loc["page"] == 2]
    j60_compact = ", ".join(f'"{source_highlighter.from_location(loc).compact().split("&id=")[0]}"' for loc in j60)
    erob = json.loads((FIXTURES / "erob80-band.locations.json").read_text(encoding="utf-8"))
    erob_compact = ", ".join(f'"{source_highlighter.from_location(loc).compact().split("&id=")[0]}"' for loc in erob)
    rated = [
        {"id": "rated-torque-label", "label": "Rated torque (Nm)", **TableCell(19, 1).to_location("text", markdown)},
        {"id": "rated-torque-value", "label": "9 N·m", **TableCell(19, 2).to_location("text", markdown)},
    ]
    return [
        {
            "key": "pdf", "title": "PDF", "file": "j60-joint.pdf",
            "what": "A datasheet scan (no text layer), page 2: the peak-torque row. Geometry-anchored boxes, drawn over PDF.js.",
            "source": {"kind": "pdf"}, "options": {"page": 2}, "locations": j60, "focus": j60[-1]["id"],
            "python": f'Source.from_file("j60-joint.pdf").highlight([{j60_compact}], "{{style}}").save("pdf.png")',
        },
        {
            "key": "html", "title": "HTML page", "file": "cubemars-ak80-9.html",
            "what": "CubeMars' AK80-9 product page, archived: scripts off, no network. A quote pinned by its prefix — "
                    "found exactly once, else reported.",
            "source": {"kind": "html"}, "options": {},
            "locations": [{"id": "peak", "label": "peak torque", **TextQuote("22", prefix="Peak torque (Nm)").to_location("html")}],
            "focus": "peak",
            "python": 'Source.from_file("cubemars-ak80-9.html").highlight(TextQuote("22", prefix="Peak torque (Nm)"), "{style}").save("html.png")',
        },
        {
            "key": "markdown-raw", "title": "Markdown, raw", "file": "cubemars-ak80-9-spec.md",
            "what": "The same spec as a markdown table, as written. TableCell counts rows from the header row and "
                    "becomes the cell's exact character span.",
            "source": {"kind": "text"}, "options": {"width": 760}, "locations": rated, "focus": "rated-torque-value",
            "python": 'Source.from_file("cubemars-ak80-9-spec.md").highlight([TableCell(19, 1), TableCell(19, 2)], "{style}").save("raw.png")',
        },
        {
            "key": "markdown-rendered", "title": "Markdown, rendered", "file": "cubemars-ak80-9-spec.md",
            "what": "The same note and the same two spans, drawn as Obsidian draws it (Zach's like-obsidian viewer, "
                    "vendored). The spans still count into the markdown; they anchor on the rendered text.",
            "source": {"kind": "markdown"}, "options": {"width": 760}, "locations": rated, "focus": "rated-torque-value",
            "python": 'Source.from_file("cubemars-ak80-9-spec.md").highlight([TableCell(19, 1), TableCell(19, 2)], "{style}")'
                      '.save("rendered.png", markdown="rendered")',
        },
        {
            "key": "image", "title": "Image / screenshot", "file": "erob80-band.jpg",
            "what": "A band of eRob80's product-page screenshot. No text layer, so the places are boxes: the rated-"
                    "torque label, the eRob80I-50 column and its value.",
            "source": {"kind": "image"}, "options": {}, "locations": erob, "focus": erob[0]["id"],
            "python": f'Source.from_file("erob80-band.jpg").highlight([{erob_compact}], "{{style}}").save("image.png")',
        },
    ]


def check_cards(cards: list[dict], work: Path) -> None:
    """Run each card's Python line (default style) on the same file; any mark it can't place stops the build."""
    for card in cards:
        shutil.copy(FIXTURES / card["file"], work / card["file"])
        code = "from source_highlighter import *\n_result = " + card["python"].format(style="highlight")
        namespace: dict = {}
        previous = Path.cwd()
        os.chdir(work)
        try:
            exec(compile(code, "<card>", "exec"), namespace)  # noqa: S102 — the page's own snippets
        finally:
            os.chdir(previous)
        result = namespace["_result"]
        if result.unresolved or not result.marks:
            raise SystemExit(f"{card['title']}: drew {len(result.marks)} marks, unresolved {result.unresolved}")


# The click-capture demo: a parameter table (what an extractor outputs) driving the viewer and driven by it, built
# only on the public API — highlight(), update(), focus() and the highlight-click event. Shown on the page verbatim.
DEMO_VALUES = [  # (id, parameter, value, the html table's row and column)
    ("peak-torque", "Peak torque", "22 N·m", 1, 4),
    ("kv", "KV", "100 rpm/V", 4, 4),
    ("ratio", "Reduction ratio", "9:1", 10, 2),
    ("rated-voltage", "Rated voltage", "48 V", 17, 2),
    ("rated-torque", "Rated torque", "9 N·m", 18, 2),
    ("rated-speed", "Rated speed", "390 rpm", 19, 2),
    ("rated-current", "Rated current", "12 A", 20, 2),
    ("weight", "Weight", "490 g", 21, 2),
]

DEMO_CODE = """\
const viewer = document.getElementById("demo-viewer");
const rows = [...document.querySelectorAll("#demo-table tbody tr")];
const all = rows.map((row) => ({id: row.dataset.id, label: row.dataset.label, ...JSON.parse(row.dataset.location)}));
const layers = (id) => [
  {locations: all, style: "highlight"},
  ...(id ? [{locations: all.filter((loc) => loc.id === id), style: "box"}] : []),
];
const marks = highlight(viewer, {source: {kind: "html", url: "lib/fixtures/cubemars-ak80-9.html"}, layers: layers(null)});

function select(id) {
  for (const row of rows) row.classList.toggle("selected", row.dataset.id === id);
  rows.find((row) => row.dataset.id === id)?.scrollIntoView({block: "nearest"});
  marks.update({layers: layers(id)});
  marks.focus(id);
}
viewer.addEventListener("highlight-click", (event) => select(event.detail.id));   // highlight → row
for (const row of rows) row.addEventListener("click", () => select(row.dataset.id)); // row → highlight"""


def demo_rows() -> str:
    rows = []
    for key, parameter, value, row, col in DEMO_VALUES:
        location = TableCell(row, col, table=1).to_location("html")
        rows.append(
            f'<tr data-id="{key}" data-label="{html.escape(parameter)} {html.escape(value)}" '
            f"data-location='{html.escape(json.dumps(location))}'><td>{html.escape(parameter)}</td>"
            f"<td>{html.escape(value)}</td><td><code>TableCell({row}, {col}, table=1)</code></td></tr>"
        )
    return "".join(rows)


GALLERY_SCRIPT = """
import { highlight, STYLES } from "./lib/source-highlighter.js";
const PDFJS = new URL("./lib/pdfjs/pdf.min.mjs", location.href).href;
const cards = JSON.parse(document.getElementById("cards-data").textContent);

const json = (value) => JSON.stringify(value).replace(/"(\\w+)":/g, "$1: ").replace(/,(?=\\S)/g, ", ");
function jsCode(card, style) {
  const options = Object.entries(card.options).map(([key, value]) => `  ${key}: ${json(value)},\\n`).join("");
  return `import { highlight } from "source-highlighter";   // js/source-highlighter.js\\n\\n` +
    `const viewer = document.getElementById("viewer");\\n` +
    `const marks = highlight(viewer, {\\n` +
    `  source: ${json({...card.source, url: card.file})},\\n` + options +
    (card.source.kind === "pdf" ? `  pdfjs: "pdf.min.mjs",\\n` : "") +
    `  locations: [\\n${card.locations.map((loc) => `    ${json(loc)},`).join("\\n")}\\n  ],\\n` +
    `  style: {name: "${style}", label: true},\\n});\\n` +
    `viewer.addEventListener("highlight-click", (event) => console.log(event.detail.id));`;
}

for (const card of cards) {
  const root = document.querySelector(`[data-card="${card.key}"]`);
  const view = root.querySelector(".view");
  const log = root.querySelector(".log");
  let style = "highlight";
  const source = {...card.source, url: `lib/fixtures/${card.file}`};
  const marks = highlight(view, {source, locations: card.locations, style: {name: style, label: true}, pdfjs: PDFJS, ...card.options});
  const show = () => {
    root.querySelector(".js-code code").textContent = jsCode(card, style);
    root.querySelector(".py-code code").textContent = card.python.replace("{style}", style);
    for (const button of root.querySelectorAll(".styles button")) button.setAttribute("aria-pressed", String(button.dataset.style === style));
  };
  marks.ready.then(({unresolved}) => {
    root.dataset.ready = unresolved.length ? "unresolved" : "ok";
    marks.focus(card.focus);
  });
  view.addEventListener("highlight-click", (event) => {
    log.textContent = `highlight-click → ${JSON.stringify({id: event.detail.id, label: event.detail.label})}`;
  });
  root.querySelector(".styles").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    style = button.dataset.style;
    show();
    marks.update(card.locations, {name: style, label: true}).then(() => marks.focus(card.focus));
  });
  show();
}

{
%DEMO%
}
"""

TESTS_SCRIPT = """
const box = document.getElementById("tests-body");
const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);
const WORD = {passed: "pass", failed: "fail", error: "error", skipped: "skip", xfailed: "xfail", xpassed: "xpass"};
fetch("tests.json", {cache: "no-store"}).then((r) => (r.ok ? r.json() : Promise.reject(r.status))).then((run) => {
  const t = run.totals;
  const bad = (t.failed || 0) + (t.error || 0);
  const parts = ["passed", "failed", "error", "skipped", "xfailed", "xpassed"].filter((k) => t[k]).map((k) => `<b>${t[k]}</b> ${k}`);
  const files = run.files.map((f) => `<tr><td><code>${esc(f.file)}</code></td>${["passed", "failed", "skipped", "xfailed"].map((k) =>
    `<td class="num">${f[k] || ""}</td>`).join("")}</tr>`).join("");
  const acceptance = run.acceptance.map((test) => {
    const counts = Object.entries(test.counts).map(([k, n]) => `<span class="pill ${k}">${n} ${WORD[k] || k}</span>`).join(" ");
    const cases = test.cases.map((c) => `<li><span class="pill ${c.outcome}">${WORD[c.outcome] || c.outcome}</span> <code>${esc(c.params || test.name)}</code>` +
      (c.message ? `<div class="msg">${esc(c.message)}</div>` : "") + `</li>`).join("");
    return `<details class="test ${test.status}"><summary><span class="dot ${test.status}"></span><code>${esc(test.name)}</code> ${counts}` +
      `<span class="why">${esc(test.doc || "")}</span></summary><ul>${cases}</ul></details>`;
  }).join("");
  box.innerHTML =
    `<p class="totals ${bad ? "red" : "green"}">${parts.join(" · ")} <span class="when">in ${run.seconds.toFixed(0)} s</span></p>` +
    `<p class="sub">On commit <a href="${esc(run.commit_url)}"><code>${esc(run.commit.slice(0, 7))}</code></a>, ` +
    `<a href="${esc(run.run_url)}">GitHub Actions run ${esc(run.run_number)}</a>, ${esc(run.ran_at)} — ${esc(run.runner)}. ` +
    `Written by the workflow from pytest's JUnit XML; nothing here is typed by hand.</p>` +
    `<h3>The acceptance suite</h3><p class="sub">${esc(run.acceptance_doc)}</p>${acceptance}` +
    `<h3>Every test file</h3><div class="scroll"><table><tr><th>file</th><th class="num">passed</th><th class="num">failed</th><th class="num">skipped</th><th class="num">xfailed</th></tr>${files}</table></div>`;
}).catch(() => {
  box.innerHTML = `<p class="sub">No CI run is published with this copy of the page. The Pages workflow ` +
    `(<code>.github/workflows/pages.yml</code>) runs the suite on every push to main and writes <code>tests.json</code> beside it.</p>`;
});
"""


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
from source_highlighter import Source, TableCell, TextQuote

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
    style_buttons = "".join(f'<button type="button" data-style="{name}">{name}</button>' for name in source_highlighter.STYLES)
    gallery_cards = "".join(
        f"""<figure class="card" data-card="{card['key']}"><div class="view" aria-label="{html.escape(card['title'])}, highlighted"></div>
<figcaption><div class="cardhead"><h3>{html.escape(card['title'])}</h3><div class="styles" role="group" aria-label="style">{style_buttons}</div></div>
<p>{html.escape(card['what'])}</p><p class="log">Click a highlight.</p>
<pre class="code js-code"><code></code></pre><pre class="code py-code"><code></code></pre></figcaption></figure>"""
        for card in cards
    )
    cards_json = json.dumps([{k: v for k, v in card.items() if k != "what"} for card in cards], ensure_ascii=False).replace("</", "<\\/")
    page_script = GALLERY_SCRIPT.replace("%DEMO%", textwrap.indent(DEMO_CODE, "  "))
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
.card .view, .demo .view {{ height:400px; overflow:auto; background:#e9e7e1; border-bottom:1px solid var(--line); padding:10px; }}
.cardhead {{ display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }}
.styles {{ display:flex; flex-wrap:wrap; gap:4px; }}
.styles button {{ font:600 12px system-ui; padding:4px 9px; border-radius:99px; border:1px solid var(--line); background:var(--panel); color:var(--ink); cursor:pointer; }}
.styles button[aria-pressed="true"] {{ background:var(--ink); color:var(--bg); border-color:var(--ink); }}
.log {{ font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--muted) !important; min-height:1.5em; overflow-wrap:anywhere; }}
.card pre + pre {{ margin-top:6px; }}
.sh-mark {{ cursor:pointer; }}
.demo {{ display:grid; grid-template-columns:minmax(0,1.4fr) minmax(0,1fr); gap:0; background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
.demo .view {{ height:460px; border-bottom:0; border-right:1px solid var(--line); }}
.demo .side {{ max-height:460px; overflow:auto; }}
.demo table {{ border:0; border-radius:0; font-size:14px; }}
.demo tbody tr {{ cursor:pointer; }}
.demo tbody tr:hover {{ background:color-mix(in srgb, var(--hl) 18%, transparent); }}
.demo tbody tr.selected {{ background:color-mix(in srgb, var(--hl) 55%, transparent); color:#1c1d21; }}
.demo td code {{ font-size:12px; color:var(--muted); }}
.demo tr.selected td code {{ color:#1c1d21; }}
@media (max-width:760px) {{ .demo {{ grid-template-columns:1fr; }} .demo .view {{ border-right:0; border-bottom:1px solid var(--line); }} }}
.totals {{ font-size:20px; margin:0 0 4px; }}
.totals.green b {{ color:#1f8a4c; }} .totals.red b {{ color:var(--accent); }}
.totals .when {{ color:var(--muted); font-size:14px; }}
.pill {{ font:600 11.5px system-ui; padding:1px 8px; border-radius:99px; white-space:nowrap; background:var(--out-bg); }}
.pill.passed {{ background:#1f8a4c22; color:#1f8a4c; }} .pill.failed, .pill.error {{ background:#e5322d26; color:var(--accent); }}
.pill.xfailed, .pill.skipped {{ background:#c9a20026; color:#8a6d00; }}
details.test {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; margin:6px 0; }}
details.test summary {{ padding:8px 12px; cursor:pointer; display:flex; flex-wrap:wrap; align-items:center; gap:6px; }}
details.test .why {{ flex-basis:100%; color:var(--muted); font-size:13.5px; padding-left:18px; }}
details.test ul {{ margin:0; padding:4px 12px 10px 30px; font-size:13px; }}
details.test li {{ margin:2px 0; }}
.dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; background:#1f8a4c; }}
.dot.failed, .dot.error {{ background:var(--accent); }} .dot.xfailed, .dot.skipped {{ background:#c9a200; }}
.msg {{ color:var(--accent); font:12px ui-monospace,monospace; white-space:pre-wrap; }}
td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
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
<p class="links"><a href="{GITHUB}">GitHub</a><a href="{GITHUB}/blob/main/docs/peps/0001-the-source-type.md">PEP 0001: the Source type</a><a href="{GITHUB}/blob/main/CONTRACT.md">CONTRACT</a><a href="#gallery">Live gallery</a><a href="#tests">Tests</a></p>
</header>

<div class="hero">{hero_media}
<p>Recorded from the real ES module in headless Chromium, on SteadyWin's GIM4305-10 page saved by <code>markdownify capture</code>
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
<tr><td><code>.capture(keep=("html","snapshot"))</code></td><td>saves the bytes: through <code>markdownify capture</code> when it is installed (every capture level, word boxes on the snapshot), else one polite request (robots.txt honoured). A no-op once saved.</td></tr>
<tr><td><code>.to_markdown()</code></td><td>markdown of it: read as is for markdown, through <code>markdownify</code> for a PDF, page or image.</td></tr>
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
highlighter (the quote find, markdownify's index); the selectors carry its answer.</p>
<div class="scroll"><table><tr><th>selector</th><th>anchor</th><th>fields</th><th>compact</th><th>borrowed from</th></tr>{selectors_table}</table></div>
<div class="pair" style="margin-top:12px">{code_block(API_SNIPPETS['selectors'])}{output_block(api['selectors'])}</div>
<h3>Nothing is dropped silently</h3>
<p class="sub">A selector that can't address this kind of source is reported with the reason, beside the marks that were drawn.</p>
<div class="pair">{code_block(API_SNIPPETS['unresolved'])}{output_block(api['unresolved'])}</div>
</section>

<section id="gallery"><h2>Gallery</h2>
<p class="sub">Every card is the drop-in viewer itself — <code>js/source-highlighter.js</code>, live in this page. Switch the style and
the code under it changes to match; click a highlight and the card logs the <code>highlight-click</code> event your app would get.
Under the JavaScript is the same call in Python, which burns the marks into a PNG (run when this page was built).</p>
<div class="grid">{gallery_cards}</div>

<h3 id="click-capture">Click capture: a parameter table that drives the viewer</h3>
<p class="sub">What a datasheet extractor shows, in miniature. Click a highlighted value and its row is selected; click a row and its
cell is boxed and scrolled to. The highlighter only displays and reports clicks — the table logic is the ten lines below, on the
public API (<code>highlight</code>, <code>update</code>, <code>focus</code>, the <code>highlight-click</code> event).</p>
<div class="demo"><div class="view" id="demo-viewer" aria-label="CubeMars AK80-9 page, highlighted"></div>
<div class="side"><table id="demo-table"><thead><tr><th>parameter</th><th>value</th><th>location</th></tr></thead>
<tbody>{demo_rows()}</tbody></table></div></div>
{code_block(DEMO_CODE)}
</section>

<section id="tests"><h2>Tests</h2>
<p class="sub">The suite, as GitHub Actions ran it on the commit this page was deployed from.</p>
<div id="tests-body"><p class="sub">Loading the CI run…</p></div></section>

<footer>
<p><b>Install</b> (Python ≥ 3.12; HTML and text also need <code>playwright install chromium</code>):</p>
{code_block('pip install "source-highlighter @ git+' + GITHUB + '"')}
<p>The interactive form is one framework-free ES module, <code>js/source-highlighter.js</code>. MIT licensed.
Datasheet pages shown belong to their makers (SteadyWin, CubeMars, and the J60 joint's maker); they appear here as the sources being cited.
This page is built by <code>scripts/build_site.py</code>; every output above was printed by the code beside it.</p>
</footer>
</main>
<script type="application/json" id="cards-data">{cards_json}</script>
<script type="module">{page_script}</script>
<script type="module">{TESTS_SCRIPT}</script>
</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gim", help=f"a folder written by `markdownify capture {GIM_URL} --keep html,snapshot --out DIR`")
    parser.add_argument("--no-hero", action="store_true", help="skip recording the hero video (keeps the old one)")
    parser.add_argument("--stage", action="store_true", help="only copy what the live gallery loads into site/lib/ (CI)")
    args = parser.parse_args()
    stage_live_assets()
    if args.stage:
        print(json.dumps({"staged": str(LIB), "bytes": sum(p.stat().st_size for p in LIB.rglob("*") if p.is_file())}))
        return
    if not args.gim:
        parser.error("--gim is required to build the page")
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
            shutil.copy(FIXTURES / name, work / name)
        shutil.copytree(gim_dir, work / "steadywin-gim4305",
                        ignore=shutil.ignore_patterns("bundle", "fragbundle", "frag.html"))
        api = {name: run_snippet(textwrap.dedent(code), work) for name, code in API_SNIPPETS.items()}
        cards = live_cards()
        check_cards(cards, work)
        hero = (MEDIA / "hero.mp4").exists()
        if not args.no_hero:
            record_hero(Source.from_bundle(work / "steadywin-gim4305"), scratch)
            hero = True
    for stale in MEDIA.glob("gallery-*.jpg"):  # the gallery was pictures until 2026-11-17; it is live now
        stale.unlink()
    (SITE / "index.html").write_text(build_page(api, cards, hero), encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    sizes = {path.name: path.stat().st_size for path in sorted(MEDIA.iterdir())}
    print(json.dumps({"site": str(SITE), "media_bytes": sizes, "total_bytes": sum(sizes.values())}, indent=1))


if __name__ == "__main__":
    main()
