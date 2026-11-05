// Source Highlighter — the interactive form (CONTRACT.md §4.2).
//
//   const marks = highlight(container, {source, locations, style});
//   await marks.ready;            // {marks, unresolved}
//   marks.focus("rated-torque");  // scroll (and optionally zoom) to a location
//   marks.update(locations, style);
//   marks.destroy();
//
// Framework-free on purpose: a static HTML page and a React app call this the same way.
// It renders the source itself (PDF.js canvas, <img>, a sandboxed <iframe> for archived HTML, a <pre> for
// text) so the marks and the pixels underneath share one coordinate system.
//
// Geometry rule shared with the Python renderer (src/source_highlighter/geometry.py) — both forms must draw the
// same rectangles, and tests measure that they do:
//   * A page/box Location becomes [x0*W - p, y0*H - p, x1*W + p, y1*H + p] in source units (PDF points, image
//     pixels), clipped to the page, then multiplied by the zoom. p = style.padding.
//   * An html/text Location becomes one rectangle per line box of its DOM Range (Range.getClientRects), padded
//     the same way, in CSS pixels at zoom 1.
//   * box: an outline `width` thick just outside the rectangle. underline: a bar `width` thick just below it.
//     spotlight / blackout: everything on the page outside the union of rectangles is covered.

export const STYLE_DEFAULTS = {
  highlight: { color: "#ffe600", opacity: 0.45, padding: 2, width: 0 },
  box: { color: "#e5322d", opacity: 1, padding: 2, width: 2 },
  underline: { color: "#e5322d", opacity: 1, padding: 2, width: 2 },
  spotlight: { color: "#000000", opacity: 0.6, padding: 2, width: 0 },
  blackout: { color: "#000000", opacity: 1, padding: 2, width: 0 },
};
export const STYLES = Object.keys(STYLE_DEFAULTS);

// WHY 1280: the visual twins are full-page screenshots taken at a 1280 px wide viewport, so an archived page
// laid out at the same width lines up with its twin as closely as an offline page can.
export const HTML_LAYOUT_WIDTH = 1280;

const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "webp", "gif", "avif", "bmp"];
const HTML_EXTENSIONS = ["html", "htm", "xhtml"];

// WHY: an archived page is untrusted third-party HTML. No scripts run (sandbox without allow-scripts) and the
// CSP blocks every network fetch, so showing a page never phones home or depends on a live site. Inline styles
// and data: images still render. allow-same-origin is what lets us place DOM Ranges inside it; it is only
// dangerous together with allow-scripts, which is never granted.
const ARCHIVED_PAGE_CSP =
  "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; media-src data:";

// WHY freeze animation: an archived page is a snapshot. A looping spinner would otherwise make two renders of the
// same page differ, and the static PNG would depend on when the screenshot happened to fire. Media cannot load
// offline (the CSP blocks it), and Chromium then draws its own loading spinner over the player, which no author
// CSS can pause — so players keep their box but are not painted.
const ARCHIVED_PAGE_PRELUDE =
  `<meta http-equiv="Content-Security-Policy" content="${ARCHIVED_PAGE_CSP}">` +
  "<style>*, *::before, *::after { animation-play-state: paused !important; animation-delay: 0s !important; transition: none !important; }" +
  " video, audio { visibility: hidden !important; }</style>";

/** The srcdoc an archived page is shown with: the prelude goes right after the doctype. */
function archivedSrcdoc(html) {
  const quiet = quietArchivedHtml(html);
  const doctype = quiet.match(/^\s*<!doctype[^>]*>/i);
  // WHY after the doctype: anything before it would flip the archived page into quirks mode.
  return doctype ? doctype[0] + ARCHIVED_PAGE_PRELUDE + quiet.slice(doctype[0].length) : ARCHIVED_PAGE_PRELUDE + quiet;
}

// Attributes that make a browser fetch (or run) something. Renamed, not deleted, so the element and its box stay.
const FETCHING_ATTRIBUTES = /^(src|srcset|poster|background|data|lowsrc|on[a-z]+)$/i;
const FETCHING_HREF_TAGS = /^(link|image|use|feimage)$/i;
const TAG = /<([a-zA-Z][^\s/>]*)((?:\s+[^\s"'=<>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?)*)(\s*\/?)>/g;
const ATTRIBUTE = /(\s+)([^\s"'=<>/]+)(\s*=\s*(?:"[^"]*"|'([^']*)'|[^\s"'>]+))?/g;
const CSS_URL = /url\(\s*(["']?)(?!data:|#)[^)]*\1\s*\)/gi;

/**
 * The archived HTML with everything that would fetch or run removed: no <script>, no <link>, no refresh, and every
 * src / srcset / poster / on… attribute (and CSS url()) disabled.
 *
 * WHY: the sandbox and CSP already block all of it, but each blocked script and asset logs a console error — an
 * archived shop page logs hundreds, which buries a caller's own errors. Only tags and attributes change, never
 * text or the element tree that renders, so the same rewrite on both parses keeps source_range exact.
 */
function quietArchivedHtml(html) {
  return html
    .replace(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi, "")
    .replace(/<style\b([^>]*)>([\s\S]*?)<\/style\s*>/gi, (_, attributes, css) => `<style${attributes}>${quietCss(css)}</style>`)
    .replace(TAG, (whole, tag, attributes, close) => {
      if (/^link$/i.test(tag)) return "";
      if (/^meta$/i.test(tag) && /http-equiv\s*=\s*["']?refresh/i.test(attributes)) return "";
      if (!attributes) return whole;
      const quiet = attributes.replace(ATTRIBUTE, (attribute, space, name, value) => {
        if (/^style$/i.test(name) && value) return space + name + quietCss(value);
        const fetches = FETCHING_ATTRIBUTES.test(name) || (/^(xlink:)?href$/i.test(name) && FETCHING_HREF_TAGS.test(tag));
        // data: URIs load nothing, and "#id" (an SVG <use> of a local symbol) points inside the page.
        if (!fetches || /^\s*=\s*["']?\s*(data:|#)/i.test(value || "")) return attribute;
        return `${space}data-sh-${name.replace(":", "-")}${value || ""}`;
      });
      return `<${tag}${quiet}${close}>`;
    });
}

function quietCss(css) {
  return css.replace(/@import[^;]*;?/gi, "").replace(CSS_URL, "none");
}

const squashSpace = (value) => (value || "").replace(/\s+/g, "");
const pageTextCache = new WeakMap();

// ---- quotes: exact first, then approximate, never picked from two places ----

// WHY 0.8: approx-string-match is asked for up to len/2 edits (Hypothesis's setting); a candidate that far off is a
// different sentence, so only one within a fifth of the quote's length counts as found.
export const QUOTE_MIN_SIMILARITY = 0.8;

/** `text` with whitespace removed, and the index in `text` each kept character came from. */
function squashWithMap(text) {
  let squashed = "";
  const map = [];
  for (let index = 0; index < text.length; index += 1) {
    if (/\s/.test(text[index])) continue;
    squashed += text[index];
    map.push(index);
  }
  return { squashed, map };
}

/**
 * Every place `quote` occurs in `text` — whitespace never counts, as with text_quote — as offsets into `text`.
 * Exact places first; only when there are none, approximate ones (approx-string-match through Hypothesis's
 * matchQuote scoring) within QUOTE_MIN_SIMILARITY, one per stretch of text.
 */
export function quoteCandidates(text, quote, matchQuoteAll) {
  const needle = squashSpace(quote);
  const { squashed, map } = squashWithMap(text);
  const toText = (start, end, errors) => ({ start: map[start], end: map[end - 1] + 1, errors });
  const exact = [];
  if (needle) for (let at = squashed.indexOf(needle); at !== -1; at = squashed.indexOf(needle, at + 1)) exact.push(toText(at, at + needle.length, 0));
  if (exact.length || !needle || !matchQuoteAll) return { exact, approximate: [] };
  const approximate = [];
  const ranked = matchQuoteAll(squashed, needle).filter((match) => match.end > match.start && 1 - match.errors / needle.length >= QUOTE_MIN_SIMILARITY);
  ranked.sort((a, b) => a.errors - b.errors || b.score - a.score);
  for (const match of ranked) {
    // The matcher reports one region several times (shifted ends); keep the best of each, so "two" means two places.
    if (approximate.some((kept) => match.start < kept.squashedEnd && match.end > kept.squashedStart)) continue;
    approximate.push({ ...toText(match.start, match.end, match.errors), squashedStart: match.start, squashedEnd: match.end });
  }
  return { exact, approximate };
}

/**
 * Pick the one place a quote is, or say why not. `found` is a list of {where, candidates} (one per page searched).
 * WHY never the best of several: datasheet values repeat (350.4W in two model columns), so a top-scoring match is
 * a guess — two or more places, exact or approximate, are reported as ambiguous.
 */
function decideQuote(quote, found, matcherLoaded) {
  const exact = found.flatMap(({ where, candidates }) => candidates.exact.map((match) => ({ where, ...match })));
  if (exact.length === 1) return { match: exact[0] };
  if (exact.length > 1) return { reason: `ambiguous: "${quote}" occurs ${exact.length} times` };
  const approximate = found.flatMap(({ where, candidates }) => candidates.approximate.map((match) => ({ where, ...match })));
  if (approximate.length === 1) return { match: approximate[0] };
  if (approximate.length > 1) return { reason: `ambiguous: "${quote}" is approximately in ${approximate.length} places` };
  return { reason: `"${quote}" is not in the source${matcherLoaded ? "" : " (exact search only: the approximate matcher did not load)"}` };
}

// ---- notes: a heading and/or a ^block in markdown ----

/**
 * A §2 note Location ({heading, block}) as [start, end) UTF-16 offsets into the markdown, or {reason}.
 * heading → its section, from the heading line to the next heading of the same or a higher level; block → the
 * paragraph that ends in " ^id" (without the marker), or the one before a line holding only "^id"; both → that
 * block inside that section. Headings and ids inside fenced code are not headings.
 */
export function noteSpan(markdown, note) {
  const { heading, block } = note || {};
  if (!heading && !block) return { reason: "a note Location needs a heading or a block" };
  const lines = [];
  let fence = null;
  for (let at = 0; at <= markdown.length; ) {
    const newline = markdown.indexOf("\n", at);
    const end = newline === -1 ? markdown.length : newline;
    const text = markdown.slice(at, end);
    const fenceMark = /^ {0,3}(`{3,}|~{3,})/.exec(text);
    const inFence = fence !== null || Boolean(fenceMark);
    if (fenceMark && fence === null) fence = fenceMark[1][0];
    else if (fenceMark && fenceMark[1][0] === fence) fence = null;
    const atx = inFence ? null : /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/.exec(text);
    lines.push({ start: at, end, text, inFence, level: atx ? atx[1].length : 0, title: atx ? atx[2].trim() : null });
    if (newline === -1) break;
    at = newline + 1;
  }
  let scope = { first: 0, last: lines.length - 1, start: 0, end: markdown.length };
  if (heading) {
    const hits = lines.map((line, index) => ({ line, index })).filter(({ line }) => line.title === heading.trim());
    if (!hits.length) return { reason: `heading "${heading}" is not in the note` };
    if (hits.length > 1) return { reason: `ambiguous: heading "${heading}" occurs ${hits.length} times in the note` };
    const { line, index } = hits[0];
    let last = lines.length - 1;
    for (let next = index + 1; next < lines.length; next += 1) {
      if (lines[next].level && lines[next].level <= line.level) {
        last = next - 1;
        break;
      }
    }
    scope = { first: index, last, start: line.start, end: lines[last].end };
    if (!block) return { start: line.start, end: line.start + markdown.slice(line.start, scope.end).trimEnd().length };
  }
  const marker = new RegExp(`(^|\\s)\\^${block.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`);
  const hits = [];
  for (let index = scope.first; index <= scope.last; index += 1) if (!lines[index].inFence && marker.test(lines[index].text)) hits.push(index);
  if (!hits.length) return { reason: `block ^${block} is not in ${heading ? `the "${heading}" section` : "the note"}` };
  if (hits.length > 1) return { reason: `ambiguous: block ^${block} occurs ${hits.length} times` };
  let last = hits[0];
  const own = lines[last].text.trim() === `^${block}`;
  if (own) last -= 1; // "^id" on its own line names the block above it
  let first = last;
  while (first - 1 >= scope.first && lines[first - 1].text.trim() && !lines[first - 1].level) first -= 1;
  if (last < first || !lines[last].text.trim()) return { reason: `block ^${block} has no text before it` };
  const blockEnd = own ? lines[last].end : lines[last].start + lines[last].text.replace(marker, "").trimEnd().length;
  return { start: lines[first].start + (lines[first].text.length - lines[first].text.trimStart().length), end: blockEnd };
}

/** The last `count` non-whitespace characters of text before `at`. */
function nonSpaceBefore(text, at, count) {
  let collected = "";
  for (let index = at - 1; index >= 0 && collected.length < count; index -= 1) if (!/\s/.test(text[index])) collected = text[index] + collected;
  return collected;
}

/** The first `count` non-whitespace characters of text from `at`. */
function nonSpaceAfter(text, at, count) {
  let collected = "";
  for (let index = at; index < text.length && collected.length < count; index += 1) if (!/\s/.test(text[index])) collected += text[index];
  return collected;
}

let instanceCounter = 0;

/** Fill in a style's defaults. Accepts "spotlight" or {name: "spotlight", color, opacity, padding, width, label}. */
export function normalizeStyle(style) {
  const spec = typeof style === "string" ? { name: style } : { ...(style || {}) };
  const name = spec.name || spec.style || "highlight";
  if (!STYLE_DEFAULTS[name]) throw new Error(`unknown style "${name}" (expected one of ${STYLES.join(", ")})`);
  const merged = { ...STYLE_DEFAULTS[name], label: false };
  for (const key of ["color", "opacity", "padding", "width", "label"]) {
    if (spec[key] !== undefined && spec[key] !== null) merged[key] = spec[key];
  }
  return { name, ...merged };
}

/** One style per call, or a list of {locations, style} layers (CONTRACT §3). */
export function normalizeLayers({ locations, style, layers }) {
  if (layers) return layers.map((layer) => ({ locations: layer.locations || [], style: normalizeStyle(layer.style || style) }));
  return [{ locations: locations || [], style: normalizeStyle(style) }];
}

/** Which renderer a source needs: "pdf" | "image" | "html" | "text". */
export function sourceKind(source) {
  if (source && typeof source === "object" && source.kind) return source.kind;
  const url = typeof source === "string" ? source : source?.url || "";
  if (typeof source === "object" && source.text !== undefined) return "text";
  if (typeof source === "object" && source.html !== undefined) return "html";
  const extension = url.split(/[?#]/)[0].split(".").pop().toLowerCase();
  if (extension === "pdf") return "pdf";
  if (IMAGE_EXTENSIONS.includes(extension)) return "image";
  if (HTML_EXTENSIONS.includes(extension)) return "html";
  return "text";
}

/** Locations count Unicode code points (CONTRACT §2); DOM offsets count UTF-16 units. Convert one to the other. */
export function codePointToUtf16(text, codePointOffset) {
  let utf16 = 0;
  let codePoints = 0;
  while (codePoints < codePointOffset && utf16 < text.length) {
    const unit = text.charCodeAt(utf16);
    utf16 += unit >= 0xd800 && unit <= 0xdbff && utf16 + 1 < text.length ? 2 : 1;
    codePoints += 1;
  }
  return codePoints === codePointOffset ? utf16 : -1;
}

/** A page/box Location as a rectangle in source units (see the geometry rule at the top). */
export function pageBoxRect(box, pageWidth, pageHeight, padding) {
  const [x0, y0, x1, y1] = box;
  return clipRect(
    { x0: x0 * pageWidth - padding, y0: y0 * pageHeight - padding, x1: x1 * pageWidth + padding, y1: y1 * pageHeight + padding },
    pageWidth,
    pageHeight,
  );
}

function clipRect(rect, width, height) {
  return {
    x0: Math.max(0, rect.x0),
    y0: Math.max(0, rect.y0),
    x1: Math.min(width, rect.x1),
    y1: Math.min(height, rect.y1),
  };
}

/** "page" (a box), "html", "text", "note", "quote" (with or without a page), "page-only" — or null. */
function locationKind(location) {
  if (!location || typeof location !== "object") return null;
  const kinds = ["box", "html", "text", "note", "quote"].filter((key) => location[key] !== undefined);
  if (!kinds.length) return location.page !== undefined ? "page-only" : null;
  if (kinds.length !== 1) return null;
  return kinds[0] === "box" ? "page" : kinds[0];
}

const LOCATION_SHAPES = 'a Location needs exactly one of "box" (with "page"), "html", "text", "note" or "quote" — or only "page"';

function isValidBox(box) {
  return (
    Array.isArray(box) &&
    box.length === 4 &&
    box.every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1) &&
    box[0] < box[2] &&
    box[1] < box[3]
  );
}

function translucent(color, opacity) {
  // WHY color-mix: callers pass any CSS colour (names, hex, rgb()), and opacity is a separate contract parameter.
  return `color-mix(in srgb, ${color} ${Math.round(opacity * 1000) / 10}%, transparent)`;
}

async function loadPdfJs(pdfjs) {
  if (pdfjs && typeof pdfjs === "object" && pdfjs.getDocument) return pdfjs;
  const moduleUrl = typeof pdfjs === "string" ? pdfjs : "pdfjs-dist";
  const library = await import(/* @vite-ignore */ moduleUrl);
  if (!library.GlobalWorkerOptions.workerSrc && typeof pdfjs === "string") {
    library.GlobalWorkerOptions.workerSrc = pdfjs.replace(/pdf(\.min)?\.mjs$/, "pdf.worker$1.mjs");
  }
  return library;
}

/**
 * The approximate matcher (vendor/match-quote.js beside this file), or null when it cannot load — a copy of this
 * one file inlined into a page, say. WHY loaded on demand: exact Locations need nothing but this file.
 */
async function loadMatcher(given) {
  if (typeof given === "function") return given;
  try {
    return (await import(/* @vite-ignore */ new URL("./vendor/match-quote.js", import.meta.url).href)).matchQuoteAll;
  } catch {
    return null;
  }
}

/**
 * Scroll `target` to the middle of each scrolling box around it, innermost first, then of its own window.
 * WHY not scrollIntoView: it also scrolls every parent frame, so a highlighter embedded in another page (a peek
 * card, a gallery iframe) would yank the host page. A fixed-position box (a popup) stops the walk: scrolling
 * what lies behind it cannot move the target.
 */
export function centerInScrollers(target, behavior = "auto") {
  const view = target.ownerDocument.defaultView;
  let shiftX = 0;
  let shiftY = 0;
  const box = target.getBoundingClientRect();
  const middle = () => ({ x: (box.left + box.right) / 2 - shiftX, y: (box.top + box.bottom) / 2 - shiftY });
  for (let node = target.parentElement; node && node !== target.ownerDocument.documentElement; node = node.parentElement) {
    const style = view.getComputedStyle(node);
    const scrollsX = /(auto|scroll|overlay)/.test(style.overflowX) && node.scrollWidth > node.clientWidth;
    const scrollsY = /(auto|scroll|overlay)/.test(style.overflowY) && node.scrollHeight > node.clientHeight;
    if ((scrollsX || scrollsY) && node !== target.ownerDocument.body) {
      const frame = node.getBoundingClientRect();
      const { x, y } = middle();
      const left = scrollsX ? Math.max(0, Math.min(node.scrollWidth - node.clientWidth, node.scrollLeft + x - (frame.left + node.clientLeft + node.clientWidth / 2))) : node.scrollLeft;
      const top = scrollsY ? Math.max(0, Math.min(node.scrollHeight - node.clientHeight, node.scrollTop + y - (frame.top + node.clientTop + node.clientHeight / 2))) : node.scrollTop;
      shiftX += left - node.scrollLeft;
      shiftY += top - node.scrollTop;
      node.scrollTo({ left, top, behavior });
    }
    if (style.position === "fixed") return;
  }
  const { x, y } = middle();
  view.scrollTo({ left: view.scrollX + x - view.innerWidth / 2, top: view.scrollY + y - view.innerHeight / 2, behavior });
}

async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`could not fetch ${url}: HTTP ${response.status}`);
  return response.text();
}

function el(tag, className, styles) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (styles) Object.assign(node.style, styles);
  return node;
}

function installBaseStyles() {
  if (document.getElementById("source-highlighter-styles")) return;
  const style = document.createElement("style");
  style.id = "source-highlighter-styles";
  style.textContent = `
.sh-root { position: relative; display: flex; flex-direction: column; align-items: flex-start; gap: 12px; }
.sh-page { position: relative; flex: none; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.25); overflow: hidden; }
.sh-page > canvas, .sh-page > img { display: block; width: 100%; height: 100%; }
.sh-scaled { position: absolute; left: 0; top: 0; transform-origin: 0 0; }
.sh-scaled > iframe { display: block; border: 0; background: #fff; }
.sh-text { margin: 0; padding: 16px; box-sizing: border-box; white-space: pre-wrap; overflow-wrap: anywhere;
  font: 14px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #1f2328; background: #fff; }
.sh-overlay { position: absolute; inset: 0; pointer-events: none; }
.sh-overlay > svg { position: absolute; left: 0; top: 0; }
.sh-mark { position: absolute; box-sizing: border-box; pointer-events: auto; }
.sh-mark.sh-underline::after { content: ""; position: absolute; left: 0; right: 0; top: 100%;
  height: var(--sh-width); background: var(--sh-color); }
.sh-label { position: absolute; transform: translateY(-100%); padding: 1px 5px; font: 600 11px/1.4 system-ui, sans-serif;
  color: #fff; background: #1f2328; border-radius: 3px; white-space: nowrap; pointer-events: none; }
.sh-mark.sh-focus { animation: sh-pulse 1.2s ease-out 1; }
@keyframes sh-pulse { 0% { box-shadow: 0 0 0 0 rgba(229,50,45,.7); } 100% { box-shadow: 0 0 0 14px rgba(229,50,45,0); } }
`;
  document.head.appendChild(style);
}

/**
 * Render `source` into `container` and draw `locations` over it in `style`.
 *
 * source: a URL string (kind from its extension) or {kind: "pdf"|"image"|"html"|"text", url?, data?, html?, text?}.
 * options: {locations, style} or {layers: [{locations, style}]}; zoom: number or "fit" (default: fit the
 *   container width); pdfjs: the pdfjs-dist module or a URL to pdf.mjs (PDFs only); width: layout width for
 *   html (default 1280) and text (default: the container width).
 * Returns {ready, update(locations, style), focus(id, {zoom}), destroy()}; `ready` resolves to
 * {marks, unresolved} once the source is on screen and the marks are drawn.
 */
export function highlight(container, options) {
  installBaseStyles();
  const instanceId = `sh${++instanceCounter}`;
  const root = el("div", "sh-root");
  root.dataset.instance = instanceId;
  container.appendChild(root);

  const state = {
    kind: sourceKind(options.source),
    layers: normalizeLayers(options),
    zoomOption: options.zoom ?? "fit",
    zoom: 1,
    pages: [], // [{element, overlay, width, height}] — width/height in source units
    destroyed: false,
    highlightNames: [],
    result: { marks: [], unresolved: [] },
  };

  async function renderSource() {
    root.replaceChildren();
    state.pages = [];
    const source = typeof options.source === "string" ? { url: options.source } : options.source;
    if (state.kind === "pdf") await renderPdf(source);
    else if (state.kind === "image") await renderImage(source);
    else if (state.kind === "html") await renderHtml(source);
    else await renderText(source);
  }

  function resolveZoom(naturalWidth) {
    if (typeof state.zoomOption === "number") return state.zoomOption;
    const padding = getComputedStyle(container);
    const available = container.clientWidth - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight);
    return (available > 0 ? available : naturalWidth) / naturalWidth;
  }

  function addPage(width, height) {
    const element = el("div", "sh-page", { width: `${width * state.zoom}px`, height: `${height * state.zoom}px` });
    element.dataset.page = String(state.pages.length + 1);
    root.appendChild(element);
    const page = { element, overlay: null, width, height };
    state.pages.push(page);
    return page;
  }

  async function renderPdf(source) {
    const pdfjs = await loadPdfJs(options.pdfjs);
    const documentSource = source.data ? { data: source.data } : { url: source.url };
    state.pdfTask = pdfjs.getDocument(documentSource);
    const pdf = await state.pdfTask.promise;
    state.pdf = pdf;
    const pageNumbers = options.page ? [options.page] : Array.from({ length: pdf.numPages }, (_, index) => index + 1);
    const first = await pdf.getPage(pageNumbers[0]);
    state.zoom = resolveZoom(first.getViewport({ scale: 1 }).width);
    state.pdfPageNumbers = pageNumbers;
    for (const pageNumber of pageNumbers) {
      if (state.destroyed) return;
      const pdfPage = pageNumber === pageNumbers[0] ? first : await pdf.getPage(pageNumber);
      const natural = pdfPage.getViewport({ scale: 1 }); // 1 CSS px per PDF point, rotation applied
      const page = addPage(natural.width, natural.height);
      page.pageNumber = pageNumber;
      page.pdfPage = pdfPage;
      page.naturalViewport = natural;
      const pixelRatio = window.devicePixelRatio || 1;
      const viewport = pdfPage.getViewport({ scale: state.zoom * pixelRatio });
      const canvas = el("canvas");
      canvas.width = Math.round(viewport.width);
      canvas.height = Math.round(viewport.height);
      page.element.appendChild(canvas);
      await pdfPage.render({ canvas, canvasContext: canvas.getContext("2d"), viewport }).promise;
    }
  }

  async function renderImage(source) {
    const image = el("img");
    image.alt = "";
    image.decoding = "sync";
    image.src = source.url;
    await image.decode();
    state.zoom = resolveZoom(image.naturalWidth);
    const page = addPage(image.naturalWidth, image.naturalHeight);
    page.pageNumber = 1;
    page.element.appendChild(image);
  }

  async function renderHtml(source) {
    const html = source.html ?? (await fetchText(source.url));
    state.rawHtml = html;
    const width = options.width || HTML_LAYOUT_WIDTH;
    const frame = el("iframe");
    frame.setAttribute("sandbox", "allow-same-origin");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.title = "Archived page";
    frame.style.width = `${width}px`;
    frame.style.height = "800px";
    frame.srcdoc = archivedSrcdoc(html);
    const scaled = el("div", "sh-scaled");
    scaled.appendChild(frame);
    const holder = addPage(width, 800);
    holder.element.appendChild(scaled);
    holder.pageNumber = 1;
    await new Promise((resolve) => frame.addEventListener("load", resolve, { once: true }));
    const frameDocument = frame.contentDocument;
    const height = Math.max(frameDocument.documentElement.scrollHeight, frameDocument.body?.scrollHeight || 0);
    frame.style.height = `${height}px`;
    holder.height = height;
    holder.frame = frame;
    state.zoom = resolveZoom(width);
    applyZoomToScaledPage(holder);
  }

  async function renderText(source) {
    const text = source.text ?? (await fetchText(source.url));
    state.text = text;
    const padding = getComputedStyle(container);
    const width = options.width || container.clientWidth - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight) || 900;
    const pre = el("pre", "sh-text", { width: `${width}px` });
    const textNode = document.createTextNode(text);
    pre.appendChild(textNode);
    const scaled = el("div", "sh-scaled");
    scaled.appendChild(pre);
    const holder = addPage(width, 100);
    holder.element.appendChild(scaled);
    holder.pageNumber = 1;
    holder.textNode = textNode;
    holder.pre = pre;
    holder.height = pre.offsetHeight;
    state.zoom = resolveZoom(width);
    applyZoomToScaledPage(holder);
  }

  function applyZoomToScaledPage(page) {
    page.element.style.width = `${page.width * state.zoom}px`;
    page.element.style.height = `${page.height * state.zoom}px`;
    page.element.querySelector(".sh-scaled").style.transform = `scale(${state.zoom})`;
  }

  // ---- resolving Locations to rectangles (source units, per page) ----

  function findPage(pageNumber) {
    const page = state.pages.find((candidate) => candidate.pageNumber === pageNumber);
    if (page) return { page };
    const pageCount = state.kind === "pdf" ? state.pdf.numPages : 1;
    return Number.isInteger(pageNumber) && pageNumber >= 1 && pageNumber <= pageCount
      ? { reason: `page ${pageNumber} is not rendered (page option)` }
      : { reason: `page ${pageNumber} does not exist (the source has ${pageCount})` };
  }

  function resolveLocation(location) {
    const kind = locationKind(location);
    if (!kind) return { reason: LOCATION_SHAPES };
    if (kind === "page" || kind === "page-only") {
      const what = kind === "page" ? "page/box" : "page-only";
      if (state.kind !== "pdf" && state.kind !== "image") return { reason: `a ${what} Location cannot address a ${state.kind} source` };
      if (kind === "page" && !isValidBox(location.box)) return { reason: "box must be [x0, y0, x1, y1] within 0–1 with x0 < x1 and y0 < y1" };
      const found = findPage(location.page ?? 1);
      if (found.reason) return found;
      // A whole page (a slide, a drawing) is outlined, never filled: there is no finer mark to make.
      return kind === "page" ? { page: found.page, boxes: [{ kind: "page", box: location.box }] } : { page: found.page, whole: true };
    }
    if (kind === "quote") return resolveQuote(location);
    if (kind === "note") {
      if (state.kind !== "text") return { reason: `a note Location addresses a markdown note, not a ${state.kind} source` };
      const span = noteSpan(state.text, location.note);
      if (span.reason) return span;
      return { page: state.pages[0], range: textRange(span.start, span.end) };
    }
    if (kind === "html") {
      if (state.kind !== "html") return { reason: `an html Location cannot address a ${state.kind} source` };
      const found = resolveHtmlRange(location.html);
      if (found.reason) return found;
      return { page: state.pages[0], range: found.range };
    }
    if (state.kind !== "text") return { reason: `a text Location cannot address a ${state.kind} source` };
    const { start, end } = location.text || {};
    const page = state.pages[0];
    const startUnit = Number.isInteger(start) && start >= 0 ? codePointToUtf16(state.text, start) : -1;
    const endUnit = Number.isInteger(end) ? codePointToUtf16(state.text, end) : -1;
    if (startUnit < 0 || endUnit < 0 || end <= start) return { reason: `text range ${start}–${end} is outside the text or empty` };
    return { page, range: textRange(startUnit, endUnit) };
  }

  /** A Range over [start, end) UTF-16 units of the text source. */
  function textRange(start, end) {
    const range = document.createRange();
    range.setStart(state.pages[0].textNode, start);
    range.setEnd(state.pages[0].textNode, end);
    return range;
  }

  /**
   * A quote to find: {page, quote} on that page of a PDF, {quote} anywhere in the source (a reference that kept
   * only its quote). Exact first, then approximate; two or more places are ambiguous (decideQuote).
   */
  function resolveQuote(location) {
    const quote = String(location.quote ?? "");
    if (!squashSpace(quote)) return { reason: "the quote is empty" };
    if (state.kind === "image") return { reason: "an image has no text to find a quote in; give a page/box Location" };
    if (location.page !== undefined && state.kind !== "pdf") return { reason: `a page + quote Location needs a PDF, not a ${state.kind} source` };
    const loaded = Boolean(state.matcher);
    if (state.kind === "pdf") {
      let pages = state.pages;
      if (location.page !== undefined) {
        const found = findPage(location.page);
        if (found.reason) return found;
        pages = [found.page];
      }
      const decided = decideQuote(quote, pages.map((page) => ({ where: page, candidates: quoteCandidates(pdfPageText(page).text, quote, state.matcher) })), loaded);
      if (decided.reason) return { reason: location.page !== undefined ? `${decided.reason} on page ${location.page}` : decided.reason };
      const { where: page, start, end, errors } = decided.match;
      const text = pdfPageText(page);
      return { page, rects: pdfTextRects(page, start, end), found: { exact: errors === 0, errors, text: text.text.slice(start, end) } };
    }
    if (state.kind === "html") {
      const frameDocument = state.pages[0].frame.contentDocument;
      const { nodes, text } = pageText(frameDocument);
      const decided = decideQuote(quote, [{ where: null, candidates: quoteCandidates(text, quote, state.matcher) }], loaded);
      if (decided.reason) return decided;
      const { start, end, errors } = decided.match;
      const range = frameDocument.createRange();
      const startPoint = pointAt(nodes, start, false);
      const endPoint = pointAt(nodes, end, true);
      range.setStart(startPoint.node, startPoint.offset);
      range.setEnd(endPoint.node, endPoint.offset);
      return { page: state.pages[0], range, found: { exact: errors === 0, errors, text: text.slice(start, end) } };
    }
    const decided = decideQuote(quote, [{ where: null, candidates: quoteCandidates(state.text, quote, state.matcher) }], loaded);
    if (decided.reason) return decided;
    const { start, end, errors } = decided.match;
    return { page: state.pages[0], range: textRange(start, end), found: { exact: errors === 0, errors, text: state.text.slice(start, end) } };
  }

  /** A PDF page's text as PDF.js extracts it, with the text item and character each code unit came from. */
  function pdfPageText(page) {
    if (page.pdfText) return page.pdfText;
    let text = "";
    const owners = [];
    page.textContent.items.forEach((item, itemIndex) => {
      if (typeof item.str !== "string") return; // a marked-content marker
      for (let charIndex = 0; charIndex < item.str.length; charIndex += 1) owners.push({ itemIndex, charIndex });
      text += item.str;
      if (item.hasEOL) {
        owners.push(null);
        text += "\n";
      }
    });
    page.pdfText = { text, owners };
    return page.pdfText;
  }

  /**
   * [start, end) of a page's text → one rectangle per text item it touches, merged per line, in PDF points.
   * A glyph box spans the font's descent to descent + 1 em (pdfminer's rule), mapped through the item's text matrix
   * and the page viewport, so rotated and flipped text works too. WHY evenly spaced characters inside one item:
   * PDF.js reports an item's width, not each glyph's; a quote that is a whole item (a table cell) is exact.
   */
  function pdfTextRects(page, start, end) {
    const { owners } = pdfPageText(page);
    const items = page.textContent.items;
    const spans = new Map();
    for (let index = start; index < end; index += 1) {
      const owner = owners[index];
      if (!owner) continue;
      const span = spans.get(owner.itemIndex) || { from: owner.charIndex, to: owner.charIndex + 1 };
      span.from = Math.min(span.from, owner.charIndex);
      span.to = Math.max(span.to, owner.charIndex + 1);
      spans.set(owner.itemIndex, span);
    }
    const lines = [];
    for (const [itemIndex, { from, to }] of spans) {
      const item = items[itemIndex];
      const style = page.textContent.styles[item.fontName] || {};
      const descent = Number.isFinite(style.descent) ? style.descent : 0;
      const [a, b, c, d, e, f] = item.transform;
      const emWidth = item.width / (Math.hypot(a, b) || 1);
      const length = item.str.length || 1;
      const [va, vb, vc, vd, ve, vf] = page.naturalViewport.transform;
      const toPage = (x, y) => {
        const px = a * x + c * y + e;
        const py = b * x + d * y + f;
        return [va * px + vc * py + ve, vb * px + vd * py + vf];
      };
      const corners = [
        toPage((emWidth * from) / length, descent),
        toPage((emWidth * to) / length, descent),
        toPage((emWidth * from) / length, 1 + descent),
        toPage((emWidth * to) / length, 1 + descent),
      ];
      const rect = {
        x0: Math.min(...corners.map(([x]) => x)),
        y0: Math.min(...corners.map(([, y]) => y)),
        x1: Math.max(...corners.map(([x]) => x)),
        y1: Math.max(...corners.map(([, y]) => y)),
      };
      const line = lines.find((other) => Math.min(other.y1, rect.y1) - Math.max(other.y0, rect.y0) > 0.5 * Math.min(other.y1 - other.y0, rect.y1 - rect.y0));
      if (line) Object.assign(line, { x0: Math.min(line.x0, rect.x0), y0: Math.min(line.y0, rect.y0), x1: Math.max(line.x1, rect.x1), y1: Math.max(line.y1, rect.y1) });
      else lines.push(rect);
    }
    return lines;
  }

  /** Load what quote Locations need before marks are drawn: the approximate matcher and PDF text. */
  async function prepare() {
    const wantsQuotes = state.layers.some((layer) => layer.locations.some((location) => location && location.quote !== undefined));
    if (!wantsQuotes) return;
    if (state.matcher === undefined) state.matcher = await loadMatcher(options.matchQuoteAll);
    if (state.kind === "pdf") {
      for (const page of state.pages) {
        if (!page.textContent) page.textContent = await page.pdfPage.getTextContent();
      }
    }
  }

  // An html Location resolves in this order (CONTRACT §2), and never to "the first place this text occurs" —
  // datasheet values repeat (the same 30A in both model columns), so a first match is wrong half the time:
  //   1. source_range → the exact DOM position, via the browser's own parser (mapSourceRange).
  //   2. xpath → the element; inside it the text_quote (unique with its prefix/suffix), else (with no quote) the
  //      whole element.
  //   3. text_quote anywhere in the page, only if exactly one place matches exact + prefix + suffix;
  //      several matches are reported as ambiguous, never picked from.
  function resolveHtmlRange(htmlLocation) {
    const frameDocument = state.pages[0].frame.contentDocument;
    const quote = htmlLocation.text_quote || {};
    const notes = [];

    if (htmlLocation.source_range) {
      // WHY first, and accepted even when an xpath points elsewhere: source_range counts into these exact bytes,
      // so where it maps is where the Location is; an xpath that disagrees is the stale one — even when it holds
      // the same quote (the other "30A" cell).
      const { range, reason } = mapSourceRange(frameDocument, htmlLocation.source_range);
      if (range) return { range };
      notes.push(reason);
    }
    const element = htmlLocation.xpath ? evaluateXPath(frameDocument, htmlLocation.xpath) : null;
    if (htmlLocation.xpath && !element) notes.push(`xpath ${htmlLocation.xpath} did not match`);
    if (element) {
      if (!quote.exact) {
        const range = frameDocument.createRange();
        range.selectNodeContents(element);
        return { range };
      }
      const matches = quoteMatches(frameDocument, quote, element);
      if (matches.length === 1) return { range: matches[0] };
      notes.push(`the xpath element holds ${matches.length} matches of "${quote.exact}" with that prefix/suffix`);
    }
    if (quote.exact) {
      const matches = quoteMatches(frameDocument, quote, frameDocument.body || frameDocument.documentElement);
      if (matches.length === 1) return { range: matches[0] };
      notes.push(
        matches.length
          ? `ambiguous: "${quote.exact}" with that prefix/suffix occurs ${matches.length} times in the page`
          : `"${quote.exact}" with that prefix/suffix is not in the page`,
      );
    }
    if (!htmlLocation.xpath && !htmlLocation.source_range && !quote.exact) notes.push("html Location has no xpath, text_quote or source_range");
    return { reason: notes.join("; ") };
  }

  /** The page's text nodes concatenated (scripts and styles left out), with each node's start offset. */
  function pageText(frameDocument) {
    if (pageTextCache.has(frameDocument)) return pageTextCache.get(frameDocument);
    // WHY no TreeWalker filter callback: inside a script-disabled (sandboxed) document Chromium refuses to run one.
    const walker = frameDocument.createTreeWalker(frameDocument.body || frameDocument.documentElement, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let text = "";
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.parentElement?.closest("script, style, noscript, template")) continue;
      nodes.push({ node, start: text.length });
      text += node.data;
    }
    const entry = { nodes, text };
    pageTextCache.set(frameDocument, entry);
    return entry;
  }

  function pointAt(nodes, offset, isEnd) {
    for (let index = nodes.length - 1; index >= 0; index -= 1) {
      const entry = nodes[index];
      if (isEnd ? entry.start < offset : entry.start <= offset) return { node: entry.node, offset: offset - entry.start };
    }
    return null;
  }

  /** Every place `quote.exact` occurs inside `scope` whose surroundings agree with its prefix and suffix. */
  function quoteMatches(frameDocument, quote, scope) {
    const { nodes, text } = pageText(frameDocument);
    // WHY whitespace-insensitive context: a quote's prefix/suffix comes from rendered text, the DOM keeps the
    // source's whitespace (or none) between cells.
    const prefix = squashSpace(quote.prefix);
    const suffix = squashSpace(quote.suffix);
    const matches = [];
    for (let at = text.indexOf(quote.exact); at !== -1; at = text.indexOf(quote.exact, at + 1)) {
      const end = at + quote.exact.length;
      if (prefix && !nonSpaceBefore(text, at, prefix.length).endsWith(prefix)) continue;
      if (suffix && !nonSpaceAfter(text, end, suffix.length).startsWith(suffix)) continue;
      const startPoint = pointAt(nodes, at, false);
      const endPoint = pointAt(nodes, end, true);
      if (!scope.contains(startPoint.node) || !scope.contains(endPoint.node)) continue;
      const range = frameDocument.createRange();
      range.setStart(startPoint.node, startPoint.offset);
      range.setEnd(endPoint.node, endPoint.offset);
      matches.push(range);
    }
    return matches;
  }

  /**
   * source_range (code points into the decoded HTML string) → a Range on the rendered page, exactly.
   *
   * WHY markers instead of a second parser library: two comments spliced into the HTML at the range's ends are
   * placed by the browser's own HTML parser, in a detached parse of the same srcdoc, so tree construction is
   * the one that built the page on screen. Each end is then carried over to the rendered page as
   * (element path, text offset) — the two parses differ only by the markers. Both sides are checked against
   * the decoded source text, so a range inside a tag, attribute, comment, script or misnested table text is
   * reported instead of guessed.
   */
  function mapSourceRange(frameDocument, sourceRange) {
    const html = state.rawHtml;
    const { start, end } = sourceRange || {};
    let startUnit = Number.isInteger(start) && start >= 0 ? codePointToUtf16(html, start) : -1;
    let endUnit = Number.isInteger(end) ? codePointToUtf16(html, end) : -1;
    if (startUnit < 0 || endUnit < 0 || end <= start) return { reason: `source_range ${start}–${end} is outside the HTML or empty` };
    // An end that falls inside a character reference (&amp;) widens to cover the whole reference.
    const referenceBefore = (unit) => /&[#a-zA-Z0-9]{0,31}$/.exec(html.slice(Math.max(0, unit - 32), unit));
    const referenceRest = (unit) => /^[#a-zA-Z0-9]*;/.exec(html.slice(unit, unit + 32));
    if (referenceBefore(startUnit) && referenceRest(startUnit)) startUnit -= referenceBefore(startUnit)[0].length;
    if (referenceBefore(endUnit) && referenceRest(endUnit)) endUnit += referenceRest(endUnit)[0].length;

    const marker = `sh-source-range-${Math.random().toString(36).slice(2)}`;
    const instrumented =
      html.slice(0, startUnit) + `<!--${marker}:start-->` + html.slice(startUnit, endUnit) + `<!--${marker}:end-->` + html.slice(endUnit);
    const parsed = new DOMParser().parseFromString(archivedSrcdoc(instrumented), "text/html");
    const walker = parsed.createTreeWalker(parsed, NodeFilter.SHOW_COMMENT);
    let startMarker = null;
    let endMarker = null;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.data === `${marker}:start`) startMarker = node;
      if (node.data === `${marker}:end`) endMarker = node;
    }
    const notText = `source_range ${start}–${end} does not point into the page's text (it falls inside a tag, attribute, comment, script or style)`;
    if (!startMarker || !endMarker || !(startMarker.compareDocumentPosition(endMarker) & Node.DOCUMENT_POSITION_FOLLOWING)) return { reason: notText };

    const decoded = new DOMParser().parseFromString(`<body>${html.slice(startUnit, endUnit)}`, "text/html").body.textContent;
    const expected = squashSpace(decoded);
    const parsedRange = parsed.createRange();
    parsedRange.setStartAfter(startMarker);
    parsedRange.setEndBefore(endMarker);
    if (squashSpace(parsedRange.toString()) !== expected) return { reason: `source_range ${start}–${end} does not cover one contiguous run of page text` };

    const startPoint = carryOver(startMarker, frameDocument, false);
    const endPoint = carryOver(endMarker, frameDocument, true);
    const range = frameDocument.createRange();
    if (startPoint && endPoint) {
      range.setStart(startPoint.node, startPoint.offset);
      range.setEnd(endPoint.node, endPoint.offset);
    }
    if (!startPoint || !endPoint || squashSpace(range.toString()) !== expected) {
      return { reason: `source_range ${start}–${end} could not be carried over to the rendered page` };
    }
    return { range };
  }

  /** A marker's position as (element path from <html>, text offset inside that element), replayed in `target`. */
  function carryOver(markerNode, targetDocument, isEnd) {
    const parent = markerNode.parentNode;
    if (!parent || parent.nodeType !== Node.ELEMENT_NODE) return null;
    const path = [];
    for (let node = parent; node.parentElement; node = node.parentElement) path.unshift(Array.prototype.indexOf.call(node.parentElement.children, node));
    let offset = 0;
    const before = parent.ownerDocument.createTreeWalker(parent, NodeFilter.SHOW_TEXT);
    for (let node = before.nextNode(); node; node = before.nextNode()) {
      if (!(node.compareDocumentPosition(markerNode) & Node.DOCUMENT_POSITION_FOLLOWING)) break;
      offset += node.data.length;
    }
    let element = targetDocument.documentElement;
    for (const index of path) {
      element = element?.children[index];
      if (!element) return null;
    }
    if (element.localName !== parent.localName) return null;
    let seen = 0;
    const texts = targetDocument.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    for (let node = texts.nextNode(); node; node = texts.nextNode()) {
      const length = node.data.length;
      // At a boundary between two text nodes a start belongs to the next node, an end to the previous one.
      if (isEnd ? seen + length >= offset : seen + length > offset) return { node, offset: offset - seen };
      seen += length;
    }
    return seen === offset ? { node: element, offset: element.childNodes.length } : null;
  }

  function evaluateXPath(frameDocument, xpath) {
    // WHY the tbody retry: an HTML parser inserts <tbody> under <table>, so an xpath written against the raw
    // markup (…/table/tr[3]) misses in the DOM (…/table/tbody/tr[3]).
    const candidates = [xpath];
    if (/\/table(\[\d+\])?\/tr/.test(xpath)) candidates.push(xpath.replace(/\/table(\[\d+\])?\/tr/g, "/table$1/tbody/tr"));
    for (const candidate of candidates) {
      try {
        const node = frameDocument.evaluate(candidate, frameDocument, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        if (node) return node;
      } catch {
        // malformed xpath: fall through to source_range and the text quote
      }
    }
    return null;
  }

  /** Rectangles of a DOM Range, in the page's source units (CSS px at zoom 1). */
  function rangeRects(page, range) {
    let originX = 0;
    let originY = 0;
    let divisor = 1;
    if (state.kind === "text") {
      // The <pre> lives in this document under a scale() transform, so its client rects are zoomed.
      const origin = page.element.getBoundingClientRect();
      originX = origin.left;
      originY = origin.top;
      divisor = state.zoom;
    }
    // In the iframe, client rects are in the frame's own (unzoomed) viewport, which starts at the page origin.
    return Array.from(range.getClientRects())
      .filter((rect) => rect.width > 0 && rect.height > 0)
      .map((rect) => ({
        x0: (rect.left - originX) / divisor,
        y0: (rect.top - originY) / divisor,
        x1: (rect.right - originX) / divisor,
        y1: (rect.bottom - originY) / divisor,
      }));
  }

  // ---- drawing ----

  function clearMarks() {
    for (const page of state.pages) {
      page.overlay?.remove();
      page.overlay = null;
    }
    for (const { registry, name } of state.highlightNames) registry.delete(name);
    state.highlightNames = [];
    for (const page of state.pages) page.highlightStyle?.remove();
  }

  function overlayFor(page) {
    if (!page.overlay) {
      page.overlay = el("div", "sh-overlay");
      page.element.appendChild(page.overlay);
    }
    return page.overlay;
  }

  function registerTextHighlight(page, layerIndex, ranges, style) {
    // WHY the CSS Custom Highlight API for html/text highlight + underline: it paints the real text runs,
    // follows line wrapping, and never touches the archived DOM.
    const host = state.kind === "html" ? page.frame.contentWindow : window;
    const hostDocument = state.kind === "html" ? page.frame.contentDocument : document;
    if (!host.CSS?.highlights || !host.Highlight) return false;
    const name = `${instanceId}-layer${layerIndex}`;
    host.CSS.highlights.set(name, new host.Highlight(...ranges));
    state.highlightNames.push({ registry: host.CSS.highlights, name });
    const css =
      style.name === "highlight"
        ? `background-color: ${translucent(style.color, style.opacity)};`
        : `text-decoration: underline ${translucent(style.color, style.opacity)} ${style.width}px; text-underline-offset: 2px;`;
    if (!page.highlightStyle || !page.highlightStyle.isConnected) {
      page.highlightStyle = hostDocument.createElement("style");
      (hostDocument.head || hostDocument.documentElement).appendChild(page.highlightStyle);
    }
    page.highlightStyle.textContent += `::highlight(${name}) { ${css} }\n`;
    return true;
  }

  function drawMarks() {
    clearMarks();
    const marks = [];
    const unresolved = [];
    state.layers.forEach((layer, layerIndex) => {
      const { style } = layer;
      const rectsByPage = new Map();
      const rangesByPage = new Map();
      layer.locations.forEach((location, locationIndex) => {
        const resolved = resolveLocation(location);
        if (resolved.reason) {
          unresolved.push({ layer: layerIndex, index: locationIndex, location, reason: resolved.reason });
          return;
        }
        const { page } = resolved;
        let rects;
        if (resolved.range) {
          rects = rangeRects(page, resolved.range).map((rect) =>
            clipRect({ x0: rect.x0 - style.padding, y0: rect.y0 - style.padding, x1: rect.x1 + style.padding, y1: rect.y1 + style.padding }, page.width, page.height),
          );
          if (!rects.length) {
            unresolved.push({ layer: layerIndex, index: locationIndex, location, reason: "the text is in the page but not laid out (hidden or empty)" });
            return;
          }
          if (!rangesByPage.has(page)) rangesByPage.set(page, []);
          rangesByPage.get(page).push(resolved.range);
        } else if (resolved.whole) {
          rects = [{ x0: 0, y0: 0, x1: page.width, y1: page.height }];
        } else if (resolved.rects) {
          rects = resolved.rects.map((rect) =>
            clipRect({ x0: rect.x0 - style.padding, y0: rect.y0 - style.padding, x1: rect.x1 + style.padding, y1: rect.y1 + style.padding }, page.width, page.height),
          );
        } else {
          rects = [pageBoxRect(resolved.boxes[0].box, page.width, page.height, style.padding)];
        }
        const id = location.id ?? `${layerIndex}:${locationIndex}`;
        for (const rect of rects) {
          const mark = { id, label: location.label, page: page.pageNumber, style: style.name, rect };
          if (resolved.found) mark.found = resolved.found; // how a quote was found: {exact, errors, text}
          marks.push(mark);
          if (!rectsByPage.has(page)) rectsByPage.set(page, []);
          rectsByPage.get(page).push({ id, label: location.label, rect, whole: Boolean(resolved.whole) });
        }
      });
      const usesTextPaint = (state.kind === "html" || state.kind === "text") && (style.name === "highlight" || style.name === "underline");
      if (usesTextPaint) {
        for (const [page, ranges] of rangesByPage) {
          if (!registerTextHighlight(page, layerIndex, ranges, style)) {
            unresolved.push({ layer: layerIndex, index: -1, location: null, reason: "this browser lacks the CSS Custom Highlight API" });
          }
        }
      }
      const pagesToDraw = style.name === "spotlight" || style.name === "blackout" ? state.pages : [...rectsByPage.keys()];
      for (const page of pagesToDraw) drawLayerOnPage(page, rectsByPage.get(page) || [], style, layerIndex, usesTextPaint);
    });
    state.result = { marks, unresolved };
    return state.result;
  }

  function drawLayerOnPage(page, entries, style, layerIndex, usesTextPaint) {
    const overlay = overlayFor(page);
    const zoom = state.zoom;
    if (style.name === "spotlight" || style.name === "blackout") {
      // WHY an SVG mask (not an even-odd path): overlapping locations must stay a single clear hole.
      const width = page.width * zoom;
      const height = page.height * zoom;
      const maskId = `${instanceId}-mask-${layerIndex}-${page.pageNumber}`;
      const holes = entries
        .map(({ rect }) => `<rect x="${rect.x0 * zoom}" y="${rect.y0 * zoom}" width="${(rect.x1 - rect.x0) * zoom}" height="${(rect.y1 - rect.y0) * zoom}" fill="black"/>`)
        .join("");
      overlay.insertAdjacentHTML(
        "beforeend",
        `<svg class="sh-veil" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">` +
          `<mask id="${maskId}" maskUnits="userSpaceOnUse" x="0" y="0" width="${width}" height="${height}">` +
          `<rect width="${width}" height="${height}" fill="white"/>${holes}</mask>` +
          `<rect width="${width}" height="${height}" fill="${style.color}" fill-opacity="${style.opacity}" mask="url(#${maskId})"/></svg>`,
      );
    }
    for (const { id, label, rect, whole } of entries) {
      const mark = el("div", `sh-mark sh-${style.name}${whole ? " sh-whole" : ""}`, {
        left: `${rect.x0 * zoom}px`,
        top: `${rect.y0 * zoom}px`,
        width: `${(rect.x1 - rect.x0) * zoom}px`,
        height: `${(rect.y1 - rect.y0) * zoom}px`,
      });
      mark.dataset.id = id;
      mark.dataset.layer = String(layerIndex);
      if (label) mark.title = label;
      const paint = translucent(style.color, style.opacity);
      const strokeWidth = `${style.width * zoom}px`;
      if (whole) {
        // Inside the page edge (the page clips anything outside), in the box colour unless the style has its own.
        const color = style.name === "box" || style.name === "underline" ? paint : STYLE_DEFAULTS.box.color;
        const inset = `${Math.max(style.width, STYLE_DEFAULTS.box.width) * zoom}px`;
        Object.assign(mark.style, { outline: `${inset} solid ${color}`, outlineOffset: `-${inset}` });
        mark.classList.remove("sh-underline");
      } else if (!usesTextPaint && style.name === "highlight") mark.style.background = paint;
      if (style.name === "box" && !whole) mark.style.outline = `${strokeWidth} solid ${paint}`;
      if (whole) {
        // nothing more: a whole page gets only its outline
      } else if (!usesTextPaint && style.name === "underline") {
        mark.style.setProperty("--sh-width", strokeWidth);
        mark.style.setProperty("--sh-color", paint);
      } else if (style.name === "underline") {
        mark.classList.remove("sh-underline");
      }
      overlay.appendChild(mark);
      if (label && style.label) {
        const chip = el("div", "sh-label", { left: `${rect.x0 * zoom}px`, top: `${rect.y0 * zoom - 3}px` });
        chip.textContent = label;
        overlay.appendChild(chip);
      }
    }
  }

  async function setZoom(zoom) {
    state.zoomOption = zoom;
    if (state.kind === "pdf" || state.kind === "image") {
      await renderSource();
    } else {
      state.zoom = resolveZoom(state.pages[0].width);
      applyZoomToScaledPage(state.pages[0]);
    }
    await prepare();
    return drawMarks();
  }

  let chain = (async () => {
    await renderSource();
    await prepare();
    return drawMarks();
  })();

  return {
    ready: chain,
    get zoom() {
      return state.zoom;
    },
    update(locations, style) {
      chain = chain.then(async () => {
        state.layers = Array.isArray(locations) || style !== undefined
          ? normalizeLayers({ locations, style: style ?? state.layers[0].style })
          : normalizeLayers(locations);
        await prepare();
        return drawMarks();
      });
      return chain;
    },
    focus(id, focusOptions = {}) {
      chain = chain.then(async () => {
        if (focusOptions.zoom !== undefined) await setZoom(focusOptions.zoom);
        const marks = root.querySelectorAll(`.sh-mark[data-id="${CSS.escape(String(id))}"]`);
        if (!marks.length) return false;
        centerInScrollers(marks[0], focusOptions.behavior || "auto");
        for (const mark of marks) {
          mark.classList.remove("sh-focus");
          void mark.offsetWidth; // restart the pulse animation
          mark.classList.add("sh-focus");
        }
        return true;
      });
      return chain;
    },
    destroy() {
      state.destroyed = true;
      // A source still loading rejects once torn down; nobody is waiting for it any more.
      chain.catch(() => {});
      clearMarks();
      state.pdfTask?.destroy()?.catch?.(() => {});
      root.remove();
    },
  };
}
