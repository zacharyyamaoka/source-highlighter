# Source Highlighter — contract v0 (draft, 2026-10-29)

One job, Unix-style: **source + locations + style in → the source, highlighted, out.** It knows nothing about who
made the locations. Markdownify's viewer, the Datasheet Extractor's viewer, RobotParts' "Go to source" and anything
later all call it the same way.
WHY its own project (Zach, 2026-10-29): "in the spirit of stable sub-assemblies the source highlighter should be its
own standalone thing … so that anything else can use it."

Because every caller hands it locations, **this contract owns the Location format**. Markdownify's `index.json`
groundings and the extractor's `values.json` groundings are Locations.

## 1. Source

A file: PDF, image (PNG/JPEG/WebP), HTML, or text (markdown / plain). In Python it is the frozen `Source` type
(`source_msgs`, PEP 0001), whose `to_dict()` is the §5 `source` object. Optionally a *visual twin* for HTML: a
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
{"page": 78, "quote": "Mixed paper 23.4%"}               // a page, and text to find on it (no box measured yet)
{"page": 33}                                             // a whole page (a slide, a drawing): shown and outlined, no finer mark
{"quote": "Rated torque (Nm) 9"}                         // only the cited text: found anywhere in the source, exactly once or reported
{"note": {"heading": "Pricing", "block": "ab12cd"}}      // a markdown / vault note: a heading and/or a ^block id
```

The last two are best-effort locations: the highlighter finds the quote on that page, or the heading/block in the
note, and marks it; found more than once, or not at all, it says so. WHY (2026-11-05, from the Tycoon CRM and ObjectDB
agents): most real citations today are "page 78, this sentence" or "[[Note#Heading]]", with no measured box.

Optional on any Location: `id`, `label` (the annotation: shown on hover / beside the mark), `href` (the interactive
form draws the mark as a link to it; added 2026-11-17) and `target`.
A geometry Location (`{page, box}`, `{page}`) may carry `sha256`: the file it was measured on (added 2026-11-17, R2).
On other bytes it is refused — the same fractions point at something else on a new revision or a recapture.
A Location that cannot be resolved on the given source is reported back, never silently skipped.

An `html` Location may also name its element by `"css": "table.spec td.value"` (added 2026-11-16, PEP 0001); it is
used when there is no `xpath` or the `xpath` misses, and only if it matches exactly one element.

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
   visual twin); text via ranges; markdown raw (`text`) or rendered (`markdown`: Zach's like-obsidian viewer, the
   Locations anchored on the same text in the rendered note). Plain JS so a React app, a vanilla static site
   (RobotParts) and anything else can use it.
   A click on a mark dispatches a bubbling, cancelable `highlight-click` event (detail: `id`, `label`, `href`,
   `location`, `layer`, `page`, `style`); a mark with an `href` is followed unless a listener cancels it. Nothing
   else: what a click means is the caller's (WHY, Zach 2026-11-16: "all this basically does is display it. BUT I do
   think we want a drop-in viewer").

Both forms take the same three inputs and must draw the same marks in the same places (tested by measuring the drawn
rectangles, not by checking that elements exist).

3. **Check and view** (§5–§6) — `source-highlighter check refs.json [--out statuses.json]` gives each Reference its §6
   status (`live` / `deviated` / `dead` / `link-only`, or `unreachable (n of 3)` before it is dead), why, when, and
   the live link; `source-highlighter view refs.json [--port 0]` serves Lens (one Reference, full page, `#ref=<id>`)
   and `peek.js` (`<script type="module" src="…/peek.js" data-refs="refs.json">` + `<span data-ref="id">`) on 127.0.0.1.

## 5. Reference — what a caller stores (draft, 2026-11-05)

A Reference is one cited thing: the source, what was kept of it, and where in it. The **caller** decides what to
cite (an AI extraction, a CRM note, an ObjectDB record); the highlighter never decides WHAT, only shows it.
WHY (Zach, 2026-11-05): "source highlight is not in charge of WHAT gets highlighted, that's up to the AI etc." — and
the same linked-source model serves datasheets, the Tycoon CRM and ObjectDB, so it is defined once, here.

```jsonc
{"id": "ref-1", "label": "Rated torque — CubeMars AK80-9",
 "source": {"url": "https://www.cubemars.com/product/ak80-9-v3-0-robotic-actuator.html",
            "retrieved_at": "2026-10-29T19:44:27Z", "sha256": "…", "media_type": "text/html",
            "stored": {"html": "/abs/source.html", "snapshot": "/abs/snapshot.jpg", "archive": "/abs/source.wacz"}},
 "locations": [ /* §2 Locations */ ],
 "quote": "Rated torque (Nm) 9"}            // optional: the cited text, used to re-find it on a changed page
```

- **Only `source` is required.** `locations` may be empty and `stored` may be `{}`: a quote-only reference
  (most CRM evidence today) is valid; §6 then finds the `quote` best-effort and says how sure it is.
- `source.url` may be a web URL, an absolute local file path (no `file://` needed), or a vault note (`"vault": "Projects/X.md"` in place of `url`, cited
  by a §2 `note` location — vault notes are always "stored": the vault is the copy).
- `where` (optional) is the human locator as the source prints it ("App. C p.78 row 23"), shown beside the mark.
- **Ids belong to the caller**, unique within its own store; never reuse an id for a different citation. Many fields
  may cite one Reference (CRM: `evidence[field].ref = "<id>"`). A caller may keep References as Python records and
  project them to this JSON (Zach's rule: Python is the ground truth).
- `stored` holds whatever capture kept — any subset of markdownify CONTRACT §2.1's levels (`html`, `snapshot`, `pdf`,
  `archive`), or nothing at all (a link-only reference). Save with `markdownify capture <url> --keep …`; a file source
  (PDF, image) is stored as itself.
- A list of References is a reference set (`refs.json`): the input of the standalone viewer.

## 6. Showing a reference — live first, stored copy as fallback

The check runs on the viewer's server (polite: robots.txt, one request per second per host), never in the browser.
Its result uses Wikipedia's `url-status` words, the existing practice (prior art note, 2026-11-05):

| status | means | the viewer shows |
|---|---|---|
| `live` | the live page or file **exactly** matches what was cited: the same sha256 for a file; for a web page every location resolves by §2 and the `quote` is found exactly once | the stored copy marked, plus *live · unchanged since <retrieved_at>* and a live link that the browser itself highlights (a Text Fragment `#:~:text=` for HTML; for a PDF `#page=N&search="<quote>"&phrase=true`, added 2026-11-24: Adobe's open parameters, which pdf.js honours and highlights, and Chrome's viewer from its 2026-11-13 main-branch commit) |
| `deviated` | reachable but no longer an exact match (the quote is gone, appears twice, or the file changed) | the best stored copy (html → snapshot → archive → pdf) marked, *page changed since <retrieved_at>*, and what changed when it can tell |
| `dead` | unreachable after three checks at least a day apart (InternetArchiveBot's rule); one or two failures read *unreachable (n of 3)*, and a robots.txt refusal reads *not checked* and never counts | the stored copy marked, *page unreachable* |
| `link-only` | nothing stored and no exact live match | the URL and quote, *nothing stored; can't show where* — never a guessed mark |

- **Never re-render a live page inside the viewer:** the mark is always drawn on a stored copy; the live side is a link
  the browser highlights natively. WHY: a cross-origin page can't be marked in place, and a re-render could drift
  from what was cited.
- **Always both links** (Robust Links / Perma.cc practice): the original URL and the dated stored copy.
- Modes `auto` (default), `live`, `stored`.

## 7. Open a source at a Location with no server (added 2026-11-24)

`open.html` opens one stored copy at its place from a plain URL — on any static host, or from a `file://` folder in a
browser that lets a page read the files beside it. It is the "see it in the raw source" button RobotParts' datasheet
cells and any other static page link to. WHY (Zach, 2026-11-24): "when I click that I want to basically be taken
directly not just to the page, but see highlighted the exact text that you got that information from."

```
open.html?src=<stored copy>&where=<place>[&where=<place>…][&quote=<the source's words>][&label=<what the value is>]
         [&url=<the original>][&retrieved=<access date>][&sha256=<of the stored copy>]
         [&kind=pdf|image|html|text|markdown][&page=N][&style=<§3 style>][&zoom=fit|N][&pdfjs=<pdf.min.mjs URL>][&embed=1]
```

| Parameter | Meaning |
|---|---|
| `src` (required) | the stored copy, a URL relative to `open.html` (or absolute; another origin needs CORS) |
| `where` (repeatable) | a place: a source_msgs compact selector (PEP 0001 §6 — `page=2&xywh=percent:61,40,7,2`, `page=2&text=350.4W`, `text=Rated%20torque-,9,-Outer`, `table=1&row=7&col=3`, …; robot_models' `Stated.where` / `.anchor` as stored), percent-encoded as one query value; or a §2 Location as JSON — an html Location carrying `source_range` + `xpath` + `text_quote` has no single compact string |
| `quote` | the source's own words for the value, shown in the header verbatim; on a PDF, HTML or text source also found and highlighted (on the page of the first `where` that names one); on an image shown only — an image has no text |
| `label`, `url`, `retrieved`, `sha256` | what the value is; the original URL (the live link, highlighted by the browser as in §6); the access date (the stored link's date); the stored copy's sha256, checked in the browser — other bytes are said, amber |
| `kind`, `page`, `style`, `zoom`, `pdfjs`, `embed` | the source kind when `src` has no telling extension; the PDF page to show; one §3 style for every mark; `fit` or a number; another pdf.js (default `pdfjs/pdf.min.mjs` beside the page); `embed=1`: only the source with the mark in the middle and an "Open ↗" link, for a preview inside another page |

- **What is drawn:** a page box or a whole page as `box`, anything text-anchored (a quote, an html range, a markdown span)
  as `highlight` — so a value with a measured box and its words shows both. `style=` overrides both.
- **Where it lands:** a PDF draws only the cited page (‹ › and ← → walk the others; the answer stays about the cited
  page), or every page when the places name several pages or none; the first mark is scrolled to the middle of the
  view, at a readable zoom (a PDF between 100 % and 200 %).
- **Truth (§2):** a place that does not resolve exactly once is listed as *not located: <why>* and nothing is drawn for
  it; the status says how many were marked, approximately or not at all. Never a guessed mark. A PDF page with no text
  layer (a drawing exported as vector art, a scan) says so — only a page box can mark it; beside a box that marked
  the place, its quote is shown, not searched, like a quote on an image.
- **No server:** no `/api/*`, no build step. From `file://`, Chromium reads nothing beside the page unless started with
  `--allow-file-access-from-files`; without it the page says so and offers the stored copy in the browser's own
  viewer at its page, and the one-line command that serves the folder.
- **Lens and Peek** run from the same folder: `lens.html?refs=<refs.json>#ref=<id>` and
  `<script type="module" src="…/peek.js" data-refs="refs.json" data-static>`; stored paths in refs.json count from
  refs.json. `source-highlighter state refs.json --out state.json` writes what the view server's `/api/state` says
  (sizes, statuses; no machine paths) for `?state=` / `data-state=`. Without `data-static` Peek asks for a view server
  first and falls back to the folder.
- **Vendoring:** `python3 scripts/vendor.py --ref main --into <app>/static/source-highlighter` writes one flat folder
  (the module, `source-msgs.js`, `open.*`, Lens, Peek, pdf.js unless `--without-pdfjs`, `LICENSE`) from a `git archive`
  of the commit, with `VENDOR.json` naming the commit and every file's sha256; `--check <folder>` re-hashes it.

