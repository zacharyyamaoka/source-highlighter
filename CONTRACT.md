# Source Highlighter — contract v0 (draft, 2026-10-29)

One job, Unix-style: **source + locations + style in → the source, highlighted, out.** It knows nothing about who
made the locations. Markdownify's viewer, the Datasheet Extractor's viewer, RobotParts' "Go to source" and anything
later all call it the same way.
WHY its own project (Zach, 2026-10-29): "in the spirit of stable sub-assemblies the source highlighter should be its
own standalone thing … so that anything else can use it."

Because every caller hands it locations, **this contract owns the Location format**. Markdownify's `index.json`
groundings and the extractor's `values.json` groundings are Locations.

## 1. Source

A file: PDF, image (PNG/JPEG/WebP), HTML, or text (markdown / plain). Optionally a *visual twin* for HTML: a
full-page screenshot taken when the page was fetched, so an archived page can be shown without its live CSS and
images (raw HTML alone does not render faithfully offline).

## 2. Location

Exactly one of:

```jsonc
{"page": 2, "box": [0.61, 0.40, 0.68, 0.42]}            // PDF page (1-based) or image (page 1); box = [x0, y0, x1, y1],
                                                        // each 0–1 of the page's width/height, origin top-left
{"html": {"source_range": {"start": 40211, "end": 40217},  // code points into the decoded HTML string
          "xpath": "/html/body/main/table[2]/tr[3]/td[2]",
          "text_quote": {"exact": "20 Nm", "prefix": "Rated torque ", "suffix": " Peak"}}}
{"text": {"start": 840, "end": 846}}                     // code points into a text source (markdown, plain text)
```

Optional on any Location: `id`, `label` (shown on hover / beside the mark).
A Location that cannot be resolved on the given source is reported back, never silently skipped.

An `html` Location resolves in this order, and never to the first place its text happens to occur (datasheet
values repeat — the same `30A` in both model columns): (1) `source_range`, when given, mapped to its exact DOM
position by the HTML parser itself, not by searching for its text — it counts into the pinned bytes, so it wins
over an `xpath` that points elsewhere (that `xpath` is treated as stale); (2) `xpath` (retried with the `<tbody>`
a parser inserts) → that element; inside it the `text_quote` if exactly one occurrence fits its
`prefix`/`suffix`, else the whole element; (3) `text_quote` anywhere in the page, only if exactly one place matches
`exact` + `prefix` + `suffix` — two or more is reported as ambiguous, never picked from.

## 3. Style

| Style | Effect | Like |
|---|---|---|
| `highlight` | translucent fill over the location (default yellow) | a highlighter pen |
| `box` | outline around the location | circling a word |
| `underline` | line under the location | |
| `spotlight` | everything else dimmed; the location stays clear | a stage spotlight |
| `blackout` | everything else blacked out; only the locations show | blackout poetry |

Parameters: `color`, `opacity`, `padding` (in source units), `label`. Several locations, one style per call — or a
list of `{locations, style}` layers.

## 4. Out

1. **Static** — a CLI and Python function that burn the marks into a copy: PDF in → PDF or PNG pages out; image in →
   image out; HTML in → a PNG of the rendered page (or of its visual twin).
   `source-highlighter <source> --locations locations.json --style spotlight --out marked.png`
2. **Interactive** — a framework-free ES module that draws the same styles over a rendered source in a web page and
   scrolls to them: `highlight(container, {source, locations, style}) → {update(locations, style), focus(id), destroy()}`.
   PDF via PDF.js; images as `<img>`; HTML via the CSS Custom Highlight API on the archived page (or boxes on its
   visual twin); text via ranges. Plain JS so a React app, a vanilla static site (RobotParts) and anything else can
   use it.

Both forms take the same three inputs and must draw the same marks in the same places (tested by measuring the drawn
rectangles, not by checking that elements exist).
