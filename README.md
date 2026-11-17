# Source Highlighter

One job: **source + locations + style in → the source, highlighted, out.** The spec is [CONTRACT.md](CONTRACT.md)
(Location format §2, styles §3, the two outputs §4). Five styles: `highlight` (yellow highlighter), `box`
(circling words), `underline`, `spotlight` (everything else dimmed), `blackout` (blackout poetry).

| Source | Static (Python / CLI) | Interactive (ES module) |
|---|---|---|
| PDF | PNG per marked page, or a vector-marked PDF (pypdfium2) | PDF.js canvas + overlay |
| Image | image of the same size (Pillow) | `<img>` + overlay |
| HTML | PNG of the page laid out by headless Chromium, or of its visual twin (`--twin`) | sandboxed `<iframe srcdoc>`; CSS Custom Highlight API |
| Text / markdown | raw: PNG, or a self-contained `.html` page; rendered (`--markdown rendered`): PNG of the note as Obsidian draws it | raw: `<pre>`; rendered (`{kind: "markdown"}`): Zach's like-obsidian viewer; CSS Custom Highlight API |

**Landing page, with the API and a gallery: <https://zacharyyamaoka.github.io/source-highlighter/>**

## The `Source` API (PEP 0001)

```python
from source_highlighter import Source, PageBox, TableCell

src = Source.from_file("tests/fixtures/j60-joint.pdf")            # or Source.from_url(url).capture(), from_text(...)
src.highlight("page=2&xywh=percent:19.2,30.9,6.4,0.8", "box").show()  # opens it; headless under pytest / no display
src.highlight(PageBox(2, 0.19, 0.31, 0.07, 0.01), "spotlight").save("marked.png")
```

`Source` is one frozen type however it is made (`from_url`, `from_file`, `from_text`, `from_bundle` for an
`markdownify capture` folder, `from_dict`); its verbs are `.capture()`, `.to_markdown()` and `.highlight(locations, style)`
→ `Highlighted`, which `.show()`s (browser, notebook, or nothing: `SOURCE_HIGHLIGHTER_BACKEND=none`) or `.save()`s.
Locations are eight selectors in two families — text-anchored `TextQuote`, `TextPosition`, `XPath`, `Css`,
`TableCell`, `Note`; geometry-anchored `PageBox`, `Page` — each with a compact string (`text=…`, `char=…`,
`page=…&xywh=percent:…`) and a CONTRACT §2 dict. The types live in `packages/source_msgs`, a stdlib-only package the
other source apps can import without this one; why and how: [docs/peps/0001-the-source-type.md](docs/peps/0001-the-source-type.md).
The landing page is built by `uv run python scripts/build_site.py --gim DIR` into `site/`; `.github/workflows/pages.yml`
runs the test suite, writes its results into the page (`scripts/ci_tests_json.py`), stages the live gallery's files
and deploys it.

## Use it

```bash
uv sync && pnpm --dir js install
uv run source-highlighter tests/fixtures/j60-joint.pdf --locations tests/fixtures/j60-joint.locations.json --style spotlight --out out/j60-spotlight.png
```

The CLI prints `{"outputs", "marks", "unresolved"}` as JSON; unresolvable Locations are also named on stderr.
Options: `--color --opacity --padding --width --label --page N --scale S --twin shot.jpg --pdf marked.pdf --layout-width W`.

```python
from source_highlighter import render
result = render("datasheet.pdf", locations, "box", "marked.png")   # locations: list, {"layers": [...]} or a .json path
result.outputs, result.marks, result.unresolved
```

```js
import { highlight } from "./source-highlighter.js";
const marks = highlight(container, {
  source: "datasheet.pdf",            // or {kind: "pdf"|"image"|"html"|"text", url | data | html | text}
  locations, style: "spotlight",      // or {layers: [{locations, style}]}; style may be {name, color, opacity, padding, width, label}
  pdfjs: "/vendor/pdfjs/pdf.min.mjs", // PDFs only: the pdfjs-dist module or its URL
  zoom: "fit",                        // or a number
});
const {marks: drawn, unresolved} = await marks.ready;
marks.focus("rated-torque", {zoom: 2});  // scroll (and optionally zoom) to a Location
marks.update(newLocations, "box");
marks.destroy();
```

### Drop it into an app

The module only displays; what a click means is the host's. Four lines:

```js
const marks = highlight(viewer, {source, locations: [{id: "kt", label: "Torque constant", href: "/parts/kt", ...where}]});
viewer.addEventListener("highlight-click", (event) => {   // bubbles from the mark; cancelable
  const {id, label, href, location, layer, page, style} = event.detail;
  event.preventDefault();                                  // take the click: an href mark is then not followed
});
```

Each Location may carry an `id`, a `label` (the annotation: the mark's tooltip, and a chip with `style.label`) and an
`href` (the mark becomes a real link, followed when no listener calls `preventDefault()`; `target` is passed on).
Selecting from outside is `update()` and `focus(id)`: the landing page's click-capture demo drives a parameter table
both ways in ten lines on these alone.

`js/` is its own npm package (`source-highlighter`, one framework-free file, no runtime dependencies;
`pdfjs-dist` is an optional peer). A React app calls it from a `useEffect`; RobotParts' static pages import it
directly. Exact Locations need only that file; the approximate quote find loads `vendor/match-quote.js` beside it
on demand (pinned in `js/vendor/VENDOR.json`) and, where it can't, stays exact-only and says so.

**Markdown, raw or rendered.** `{kind: "text"}` shows a markdown file as written. `{kind: "markdown"}` draws it as
Obsidian does, through Zach's [like_obsidian_md_viewer](https://github.com/zacharyyamaoka/like_obsidian_md_viewer) (`mountNote`, read-only,
light), vendored built in `js/like-obsidian/` from a commit of its `main` by `scripts/vendor_like_obsidian.py` (commit
and every file's sha256 in `VENDOR.json` / `manifest.json`); pass `markdown: <module or URL>` to use another copy. It is
drawn in an iframe as tall as the note, so every line is laid out. Locations still count into the markdown: a
`{text}` span or `{note}` anchors on the same text in the rendered note — the k-th place that text occurs in the source
is its k-th place on screen when both count the same; when they don't (the text also sits in a link target or
frontmatter), or it is not shown at all, it is reported, never guessed. A `{quote}` is found in the rendered text.
Static: `render(..., markdown="rendered")`, CLI `--markdown rendered`.

Locations (CONTRACT §2): `{page, box}`, `{html}`, `{text}`, and the best-effort ones —
`{note: {heading, block}}` (a markdown section or `^block` paragraph), `{page, quote}` (a quote on a PDF page),
`{quote}` (a quote anywhere: a reference that kept only its quote) and `{page}` (the whole page, outlined just
inside its edge, never filled). A `{page, box}` or `{page}` may carry the `sha256` of the file it was measured on
(`PageBox(..., sha256=src.sha256)`); on other bytes it is refused, never painted — both forms. A quote is found exactly first (whitespace never counts), then approximately
(approx-string-match through Hypothesis's `matchQuote` scoring, within a fifth of its length); found in two or
more places it is reported as ambiguous, never picked from. Marks of a found quote carry `found: {exact, errors,
text}`. `focus(id)` scrolls the mark to the middle of its own scroll boxes and window, never a parent frame's.

## Check a reference set (CONTRACT §6)

```bash
uv run source-highlighter check refs.json --out statuses.json
```

Each Reference gets `live` (same sha256; or, for a page, every html Location resolves and the quote is found
exactly once), `deviated` (and why), `unreachable (n of 3)` → `dead` (three failures at least a day apart, carried
in the `--out` file), or `link-only` (nothing stored, no exact live match), with `checked_at` and the live link
the browser highlights itself (`#:~:text=` for HTML, `#page=N` for a PDF). Polite: robots.txt and one request per
second per host (rules copied from markdownify's `fetch.py`), one request per URL. `--vault DIR` resolves
`source.vault` notes. Stored paths in refs.json may be absolute or relative to the refs.json.

## View a reference set

```bash
uv run source-highlighter view refs.json --port 0
```

Serves on 127.0.0.1, prints the URL, opens no browser. `/` is **Lens**: one Reference full page (`#ref=<id>`),
its stored copy marked (html → snapshot → pdf, or the vault note), ‹ › and ← → through the set, the status pill
and both links up top, and a folding, resizable **Details** panel (status and why, links, the fallback chain with
sizes, the Locations and any that did not resolve). Statuses come from `--statuses` or a `statuses.json` beside
the refs; `--vault DIR` for vault notes. **Peek** embeds the same in any page:

```html
<script type="module" src="http://127.0.0.1:PORT/peek.js" data-refs="http://127.0.0.1:PORT/refs.json"></script>
…rated at <span data-ref="ref-1">9 N·m</span>
```

Hover shows a crop at the mark with its status; click pins a panel (move by its header, resize by its corner, both
remembered); "Open full" goes to Lens. The server answers CORS only for localhost origins and hands out only the
files the set names; an archived page opened from it runs under a sandbox CSP.

## Demo

```bash
uv run python scripts/build_demo.py
python3 -m http.server 8763 --bind 127.0.0.1 --directory /home/bam/source-highlighter/demo
```

`build_demo.py` copies the module, PDF.js and the sources into `demo/` (gitignored). The page has a source
picker (PDF / IMAGE / HTML / TWIN / TEXT), a style picker, colour, zoom, labels, and a Location list that
calls `focus(id)`.

## Geometry — one rule, both forms

Lengths are in **source units**: PDF points, image pixels, CSS pixels for HTML and text; a render at zoom or
scale `s` multiplies them by `s`.

- page/box → `[x0·W − p, y0·H − p, x1·W + p, y1·H + p]`, clipped to the page (`p` = padding, default 2).
- html/text → one rectangle per line box of the DOM Range (`Range.getClientRects`), padded the same way.
- `box`: an outline `width` thick just outside the rectangle; `underline`: a line under each line of the target, end
  to end (PDF and image: a bar `width` thick just below the padded box; HTML and text: the text's own underline, unbroken
  at descenders); `spotlight` / `blackout`: everything on the page outside the union of rectangles is covered.
- Marks of one style that overlap show one tint, like a highlighter pen; separate layers stack.
- Defaults (`STYLE_DEFAULTS`, the same table in Python and JS, test-enforced): highlight `#ffe600` at 0.45,
  box/underline `#e5322d` 2 units wide, spotlight black at 0.6, blackout black at 1.

HTML Locations resolve in the order CONTRACT §2 fixes — `source_range` → xpath (with a `<tbody>` retry) →
a `text_quote` that matches exactly once — and never by first textual occurrence; a quote that fits two places is
reported as ambiguous, and an xpath that disagrees with the `source_range` is treated as stale. The static form
draws through the same module in Chromium, so both forms share the one resolver. `source_range` is mapped exactly: two marker comments are spliced in at its ends and the
browser's own HTML parser places them (in a detached parse of the same srcdoc), then each end is carried over to
the rendered page as element path + text offset and checked against the decoded source text. A range inside a
tag, attribute, comment, script or style is reported, not guessed.

Archived HTML is shown with no scripts (`sandbox="allow-same-origin"`, never `allow-scripts`), a CSP that blocks
every network fetch, animations paused and media players unpainted, so it never phones home and renders the same
way every time. Before injection its `<script>`s, `<link>`s and every fetching attribute (`src`, `srcset`,
`poster`, `on…`, CSS `url()`) are disabled — tags and attributes only, never text — so a shop page logs nothing
instead of hundreds of blocked-asset errors, and `source_range` stays exact.

## Tests

```bash
uv run pytest -q
```

They measure, not assert presence: predicted pixel values for every static style, `getBoundingClientRect` of
every drawn mark against rectangles computed outside the module (pdfium page sizes, Pillow image sizes, the
test's own DOM ranges, pdfplumber's character boxes for PDF quotes) at two zoom levels, and Python-vs-JS
rectangles for the same input. The check runs against a local fixture server; the viewer tests drag every panel
boundary and re-measure it after a reload. Nothing skips: the eRob80 and CubeMars screenshots are committed as 800-px
bands around their Locations (`scripts/crop_snapshots.py`). `scripts/make_fixtures.py` regenerates the Locations.
`tests/test_acceptance.py` is the ruler (oracle: markdownify's round-trip truth in `tests/fixtures/roundtrip/`).
The Pages workflow runs the whole suite on every push and the landing page shows the result, per test, with the commit.

## Stock parts

| Stock part | Used for | License |
|---|---|---|
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (PDFium) | PDF page rendering; vector marks in the PDF output | Apache-2.0 / BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | compositing marks into page images and images | MIT-CMU |
| [Playwright](https://playwright.dev/python/) + Chromium | laying out HTML and text for the static PNG | Apache-2.0 (Chromium: BSD-3-Clause) |
| [PDF.js](https://mozilla.github.io/pdf.js/) (`pdfjs-dist`) | PDF rendering in the browser | Apache-2.0 |
| [CSS Custom Highlight API](https://developer.mozilla.org/en-US/docs/Web/API/CSS_Custom_Highlight_API) | painting html/text highlight + underline without touching the DOM | web platform |
| SVG `<mask>` | the spotlight / blackout veil with location-shaped holes | web platform |
| `<iframe sandbox srcdoc>` + CSP | showing an archived page offline and script-free | web platform |
| [approx-string-match](https://github.com/robertknight/approx-string-match-js) 2.0.1 (vendored) | the approximate quote find | MIT |
| Hypothesis [`matchQuote`](https://github.com/hypothesis/client/blob/main/src/annotator/anchoring/match-quote.ts) (vendored, wrapped so ambiguity is reported) | scoring approximate quote matches | BSD-2-Clause |
| [like_obsidian_md_viewer](https://github.com/zacharyyamaoka/like_obsidian_md_viewer) (Zach's fork of [atomic-editor](https://github.com/kenforthewin/atomic-editor); CodeMirror 6), built and vendored | rendered markdown | MIT |
| [Text Fragments](https://developer.mozilla.org/en-US/docs/Web/URI/Reference/Fragment/Text_fragments) | the live link: the browser highlights the quote itself | web platform |
| numpy, pdfplumber, pytest (dev only) | pixel measurements, fixture generation, tests | BSD-3 / MIT / MIT |

PyMuPDF (AGPL) is deliberately not used. rough-notation (MIT) would give box/underline a hand-drawn look; v0
draws plain rectangles so the two forms can be measured against each other.
