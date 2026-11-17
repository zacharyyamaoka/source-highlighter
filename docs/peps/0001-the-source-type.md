# 0001 · The `Source` type: one thing every source app passes around

- **Date:** 2026-11-16 · **Status:** accepted and built (`packages/source_msgs`, re-exported by `source_highlighter`).
- **Source:** Zach, 2026-11-16 (vault `PROJECT - Source Highlighter.md`): "I want like a generic "source" type that I
  can easily pass into any of these things … defining a "standard" with set fields so everything can communicate
  together." And: "So how do I make a source right? maybe entry points but all the same output. Whether its URL, or
  file, etc. … Ingest/capture, then there is a .to_markdown(), and then also you can like "highlight" a source and
  then also like display highlights … The Source becomes like this central data type … in functional programming we
  do API by types."
- **Unchanged:** the highlighter's one job, *source + locations + style in → the source, highlighted, out*, and the
  CONTRACT §2 Location JSON. `render(path, locations, style, out)`, the CLI, the ES module, `check` and `view` keep
  working as they were; every test that passed before passes now.

## 1. Criteria (weights first)

| # | Criterion | Weight |
|---|---|---|
| C1 | **Importable by all three apps without importing each other.** markdownify, datasheet-extractor and source-highlighter can each depend on it; it pulls in nothing heavy (stdlib only, like robot_msgs' `dataclass-values` layer) | 25 |
| C2 | **Every entry point gives the same frozen type**, and it round-trips to the JSON callers already store (CONTRACT §5 `source`, mdify's `source.json`) | 20 |
| C3 | **Nothing breaks:** old call sites, wire format and tests unchanged | 15 |
| C4 | **Few verbs, matplotlib-easy:** `capture`, `to_markdown`, `highlight`, `show`/`save`; `show()` never steals focus in tests | 15 |
| C5 | **Locations are a small union of standard selectors**, each with a compact string, converting where it can | 15 |
| C6 | **Ships today:** no new public repo (a first public push is Zach's call), one `git revert` undoes it | 10 |

## 2. Decision in five lines

1. `Source` is one frozen dataclass: `origin` (URL or path), `kind` (`pdf` / `html` / `image` / `text`),
   `media_type`, `sha256`, `path` (the saved bytes), `retrieved_at`, `stored` (other capture levels), `title`.
   A link-only Source has no `sha256` and no `path` yet.
2. Constructors, one output: `Source.from_url`, `from_file`, `from_text`, `from_bundle` (an `mdify` capture folder),
   `from_dict`. Verbs: `.capture()`, `.to_markdown()`, `.highlight(locations, style)` → `Highlighted`, whose
   `.show()` / `.save()` draw it.
3. Locations: eight frozen selectors in two families (text-anchored, geometry-anchored) — `TextQuote`, `TextPosition`, `PageBox`, `Page`, `XPath`, `Css`, `TableCell`,
   `Note` — each with `.compact()` (a fragment-style string) and `.to_location(kind)` (the CONTRACT §2 dict).
   `parse("page=2&xywh=percent:61,40,7,2")` reads them back.
4. It lives in a new **stdlib-only distribution `source-msgs`** (import `source_msgs`), kept for now as a
   subdirectory of this public repo (`packages/source_msgs`); `source_highlighter` depends on it and re-exports it.
5. `.capture()` and `.to_markdown()` use `mdify` **when it is installed** (an optional extra), else a polite stdlib
   fetch / plain read; `.show()` and `.save()` import `source_highlighter` lazily. The core never imports a sibling.

## 3. Prior art, and what was borrowed

| Prior art | What it does | Taken here |
|---|---|---|
| [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) §4.2 | A *SpecificResource* = a *source* + a *selector* (+ a *styleClass*) | `Highlighted` = `Source` + selectors + style; the separation of "what" (source) from "where" (selector) |
| Web Annotation `TextQuoteSelector` | `exact` + `prefix` + `suffix` | `TextQuote(exact, prefix, suffix)`; the HTML `text_quote` |
| Web Annotation `TextPositionSelector` | `start` / `end` code points | `TextPosition(start, end)`; HTML `source_range` |
| Web Annotation `FragmentSelector` | a media-type fragment as the selector: PDF `#page=…&viewrect=…`, Media Fragments `#xywh=…`, RFC 5147 `#char=…` | the **compact strings**: `page=2&xywh=percent:61,40,7,2`, `char=840,846`, `page=33` |
| Web Annotation `CssSelector` / `XPathSelector` | a DOM element by CSS or XPath | `Css(selector)`, `XPath(path)` |
| Web Annotation `SvgSelector` | any shape | **not taken**: every location today is a rectangle or a range; a polygon can join the union later |
| [Text Fragments](https://wicg.github.io/scroll-to-text-fragment/) | `#:~:text=prefix-,exact,-suffix`, which every Chromium highlights | `TextQuote.compact()` *is* that syntax, so it pastes onto a live URL |
| [Hypothesis anchoring](https://web.hypothes.is/blog/fuzzy-anchoring/) | try position, then the quote with its context, fuzzy; never trust one selector | the resolver order (CONTRACT §2: `source_range` → xpath/css → quote, ambiguity reported); its `matchQuote` is already vendored |
| [PDF.js](https://mozilla.github.io/pdf.js/) | pages 1-based, viewport boxes, `#page=N` open parameters | `PageBox`/`Page` pages are 1-based; boxes are 0–1 of the page, so they survive any zoom |
| matplotlib `show()` / `savefig()` | one call opens the figure; `MPLBACKEND=Agg` makes it headless; notebooks show it inline via `_repr_png_` | `Highlighted.show()` (browser or notebook), `SOURCE_HIGHLIGHTER_BACKEND=none` (headless), `.save(path)`, `_repr_png_` |
| robot_msgs | frozen dataclasses, a `FrozenDict` for free-form context, `.show()` behind an optional extra the core never imports | the same shape: frozen fields, `stored` is a `FrozenDict`, heavy verbs import lazily |
| mdify `source.json`, CONTRACT §5 `source` | sha256, media type, retrievals, stored levels | the field names (`sha256`, `media_type`, `retrieved_at`, `stored`); `to_dict()` writes a valid §5 `source` |

## 4. Where `Source` lives: three options, scored

Scores 1–5 per criterion, × weight, /5 → out of 100.

| Option | C1 (25) | C2 (20) | C3 (15) | C4 (15) | C5 (15) | C6 (10) | Total |
|---|---|---|---|---|---|---|---|
| **A.** Inside `source_highlighter` | 1 — importing it pulls Playwright, pypdfium2 and Pillow into markdownify | 5 | 5 | 5 | 5 | 5 | **76** |
| **B.** New stdlib-only `source-msgs`, a subdirectory distribution of this repo for now | 5 | 5 | 5 | 4 — verbs import lazily, with an error naming the extra | 5 | 5 | **97** |
| **C.** In `robot_msgs` (or `dataclass-values`) | 3 — robot_msgs needs numpy; dataclass-values is about numbers | 4 | 5 | 4 | 4 | 3 — a release of a package with other users | **73** |

**B wins.** WHY a subdirectory, not its own repo: making a new public repo is a first public push (Zach's call); a
subdirectory of the already-public repo installs with
`pip install "source-msgs @ git+https://github.com/zacharyyamaoka/source-highlighter#subdirectory=packages/source_msgs"`,
and moving it to its own repo later is a `git subtree split` with no import changes.

### The shape of `Source`, briefly

| Shape | Score | Why |
|---|---|---|
| **One frozen class with a `kind` tag** (chosen) | 92 | One type to accept, one JSON form; `kind` drives the conversions; mirrors §5's single `source` object |
| A class per kind (`PdfSource`, `HtmlSource`, …) | 70 | Callers would `isinstance`-switch; a link-only URL doesn't know its kind until captured |
| The W3C JSON-LD `Annotation` objects themselves | 55 | Verbose (`@context`, `type`, IRIs); nobody among the 13 extraction tools surveyed uses it (markdownify CONTRACT §3) |

## 5. The API

```python
from source_highlighter import Source, PageBox, TextQuote, TableCell

src = Source.from_file("tests/fixtures/j60-joint.pdf")             # or from_url(...).capture(), from_text(...)
src.highlight(PageBox(2, 0.192, 0.309, 0.064, 0.008), "box").show()   # opens it; headless in tests
src.highlight("page=2&xywh=percent:19.2,30.9,6.4,0.8").save("marked.png")  # the same Location as a compact string
```

- **Constructors:** `from_url(url)` (link only, no network), `from_file(path)` (reads, hashes, sniffs the kind),
  `from_text(text, media_type="text/markdown")` (saved, content-addressed, under `$SOURCE_MSGS_CACHE` or
  `~/.cache/source-msgs`), `from_bundle(dir)` (an `mdify capture` / `mdify` folder), `from_dict(d)`.
- **`id`** is the `sha256` once bytes are saved; a link-only Source's `id` is the sha256 of its URL (different
  content, different id).
- **`capture(keep=("html", "snapshot"))`** — a no-op when bytes are saved; a local path is read; a web page goes through
  `mdify.capture` when `mdify` is importable (every capture level, the snapshot's word boxes), else one polite stdlib
  request (robots.txt, the same refused hosts as `check`) saved under the cache.
- **`to_markdown()`** — text/markdown is read as is; anything else goes through `mdify` when installed, else an
  error naming the extra.
- **`highlight(locations, style="highlight")`** → `Highlighted(source, locations, style)`, plain data, nothing drawn.
  `locations` may be one or a list of selectors, compact strings or CONTRACT §2 dicts, or `{"layers": …}`.
- **`Highlighted.save(path, **render_options)`** → the same `RenderResult` as `render()`. A selector that cannot be
  turned into a Location for this source kind is reported in `result.unresolved` with why — never dropped.
- **`Highlighted.show()`** draws it to a temporary PNG plus a small HTML page (the image, its marks, anything
  unresolved) and opens that in the browser with `autoraise=False`; inside a notebook it displays inline instead.
  The backend is `SOURCE_HIGHLIGHTER_BACKEND` = `browser` | `notebook` | `none`; with `none`, or no display, or under
  pytest, it opens nothing and returns the page's path — the headless path. `.save()` is always headless.
- An HTML Source whose capture kept a `snapshot` draws `PageBox` Locations on that snapshot (the visual twin).

## 6. Locations: the union

**Two families** (Zach, 2026-11-16). *Text-anchored* selectors point into a source's underlying text — HTML, markdown,
a PDF's text layer: `TextQuote`, `TextPosition`, `XPath`, `Css`, `TableCell`, `Note`. *Geometry-anchored* selectors
point at a rendered surface that has no text — a photo, a scan, a screenshot: `PageBox`, `Page`. Each selector says
which (`selector.anchor == "text" | "geometry"`). Both are first-class: a geometry-only highlight of an HTML page is
drawn on its capture-time snapshot.

**Resolving is a separate step, below the highlighter.** Turning "this text" (a value an AI extracted, a quote a CRM
kept) into a source + location is a resolver's job — mdify's `index.json` (every cell's range and box), the quote
find (exact, then Hypothesis-style approximate, ambiguity reported), the `TextQuote`-with-context pinning in markdown.
The selectors carry a resolver's answer; the highlighter only draws it, and reports what it could not place.

| Selector | Fields | Compact string | PDF | Image | HTML | Text / markdown |
|---|---|---|---|---|---|---|
| `TextQuote` | exact, prefix, suffix, page | `text=Rated%20torque-,20%20Nm,-Peak` (Text Fragments), `page=78&text=…` | `{page, quote}` / `{quote}` (interactive) | — | `html.text_quote` | `{text: span}` when prefix/suffix pin it; else `{quote}` |
| `TextPosition` | start, end (code points) | `char=840,846` (RFC 5147) | — | — | `html.source_range` | `{text}` |
| `PageBox` | page, x, y, w, h (0–1) | `page=2&xywh=percent:61,40,7,2` (Media Fragments, decimals allowed) | `{page, box}` | `{page, box}` | on the stored snapshot | — |
| `Page` | page | `page=33` (PDF open parameters) | `{page}` | `{page}` | — | — |
| `XPath` | path | `xpath=/html/body/table/tr[3]/td[2]` | — | — | `html.xpath` | — |
| `Css` | selector | `css=table.spec%20td` | — | — | `html.css` (new, CONTRACT §2) | — |
| `TableCell` | table, row, col (1-based; row 1 is the header row) | `table=1&row=3&col=2` | needs the cell's box (mdify `index.json`) | — | an xpath to the n-th table's cell | the cell's `{text}` span in a GFM pipe table |
| `Note` | heading, block | `heading=Pricing&block=ab12cd` | — | — | — | `{note}` |

Every selector also takes keyword-only `id` and `label` (CONTRACT §2's optional fields). A dash means "cannot
address this kind": `save()` lists it as unresolved, with the reason.

## 7. Migration (not done this round)

- **markdownify:** `mdify.capture()` returns `Captured`; `Source.from_bundle(captured.directory)` already reads its
  folder, so the first step is for mdify to *also* return `.source`. Its own `converters.Source(path, sha256,
  media_type, url)` is a subset of `Source`: replace it with an import of `source_msgs.Source` once `source-msgs`
  is a dependency. `index.json` groundings stay CONTRACT §2 dicts; `source_msgs.from_location()` reads them.
- **datasheet-extractor:** its `option2.Source` is a checker's working state, not a source record — keep it, rename
  it. The published `values.json` should carry `source: Source.to_dict()` beside each value's `grounding`, so a value
  and its Reference are one `Highlighted` away from being shown.
- **robotparts `Spec` / dataclass-values `Value.source`:** a `Spec(page=, quote=)` is a `TextQuote(quote, page=)`;
  the "Copy as Spec" step can write `source=Source.from_url(...).to_dict()` and the compact selector string.
- **Splitting the repo:** when Zach makes it public on its own, `git subtree split -P packages/source_msgs`.

## 8. Undo

Everything here is additive: `git revert` of the commit that added `packages/source_msgs` and the new modules.
The CONTRACT §2 addition (`html.css`) is one more optional key; old Locations never carry it.
