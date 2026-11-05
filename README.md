# Source Highlighter

One job: **source + locations + style in → the source, highlighted, out.** The spec is [CONTRACT.md](CONTRACT.md)
(Location format §2, styles §3, the two outputs §4). Five styles: `highlight` (yellow highlighter), `box`
(circling words), `underline`, `spotlight` (everything else dimmed), `blackout` (blackout poetry).

| Source | Static (Python / CLI) | Interactive (ES module) |
|---|---|---|
| PDF | PNG per marked page, or a vector-marked PDF (pypdfium2) | PDF.js canvas + overlay |
| Image | image of the same size (Pillow) | `<img>` + overlay |
| HTML | PNG of the page laid out by headless Chromium, or of its visual twin (`--twin`) | sandboxed `<iframe srcdoc>`; CSS Custom Highlight API |
| Text / markdown | PNG, or a self-contained `.html` page | `<pre>`; CSS Custom Highlight API |

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

`js/` is its own npm package (`source-highlighter`, one framework-free file, no runtime dependencies;
`pdfjs-dist` is an optional peer). A React app calls it from a `useEffect`; RobotParts' static pages import it
directly.

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
- `box`: an outline `width` thick just outside the rectangle; `underline`: a bar `width` thick just below it;
  `spotlight` / `blackout`: everything on the page outside the union of rectangles is covered.
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
way every time.

## Tests

```bash
uv run pytest -q
```

They measure, not assert presence: predicted pixel values for every static style, `getBoundingClientRect` of
every drawn mark against rectangles computed outside the module (pdfium page sizes, Pillow image sizes, the
test's own DOM ranges) at two zoom levels, and Python-vs-JS rectangles for the same input. The eRob80 and
CubeMars screenshots are not committed (1.2–1.4 MB); their tests skip unless `SOURCE_HIGHLIGHTER_SNAP` points at
them. `scripts/make_fixtures.py` regenerates the Locations.

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
| numpy, pdfplumber, pytest (dev only) | pixel measurements, fixture generation, tests | BSD-3 / MIT / MIT |

PyMuPDF (AGPL) is deliberately not used. rough-notation (MIT) would give box/underline a hand-drawn look; v0
draws plain rectangles so the two forms can be measured against each other.
