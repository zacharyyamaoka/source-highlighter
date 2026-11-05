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
  const doctype = html.match(/^\s*<!doctype[^>]*>/i);
  // WHY after the doctype: anything before it would flip the archived page into quirks mode.
  return doctype ? doctype[0] + ARCHIVED_PAGE_PRELUDE + html.slice(doctype[0].length) : ARCHIVED_PAGE_PRELUDE + html;
}

const squashSpace = (value) => (value || "").replace(/\s+/g, "");
const pageTextCache = new WeakMap();

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

function locationKind(location) {
  const kinds = ["box", "html", "text"].filter((key) => location && location[key] !== undefined);
  if (kinds.length !== 1) return null;
  return kinds[0] === "box" ? "page" : kinds[0];
}

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

  function resolveLocation(location) {
    const kind = locationKind(location);
    if (!kind) return { reason: 'a Location needs exactly one of "box" (with "page"), "html" or "text"' };
    if (kind === "page") {
      if (state.kind !== "pdf" && state.kind !== "image") return { reason: `a page/box Location cannot address a ${state.kind} source` };
      if (!isValidBox(location.box)) return { reason: "box must be [x0, y0, x1, y1] within 0–1 with x0 < x1 and y0 < y1" };
      const pageNumber = location.page ?? 1;
      const page = state.pages.find((candidate) => candidate.pageNumber === pageNumber);
      if (!page) {
        const pageCount = state.kind === "pdf" ? state.pdf.numPages : 1;
        return pageNumber >= 1 && pageNumber <= pageCount
          ? { reason: `page ${pageNumber} is not rendered (page option)` }
          : { reason: `page ${pageNumber} does not exist (the source has ${pageCount})` };
      }
      return { page, boxes: [{ kind: "page", box: location.box }] };
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
    const range = document.createRange();
    range.setStart(page.textNode, startUnit);
    range.setEnd(page.textNode, endUnit);
    return { page, range };
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
        } else {
          rects = [pageBoxRect(resolved.boxes[0].box, page.width, page.height, style.padding)];
        }
        const id = location.id ?? `${layerIndex}:${locationIndex}`;
        for (const rect of rects) {
          marks.push({ id, label: location.label, page: page.pageNumber, style: style.name, rect });
          if (!rectsByPage.has(page)) rectsByPage.set(page, []);
          rectsByPage.get(page).push({ id, label: location.label, rect });
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
    for (const { id, label, rect } of entries) {
      const mark = el("div", `sh-mark sh-${style.name}`, {
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
      if (!usesTextPaint && style.name === "highlight") mark.style.background = paint;
      if (style.name === "box") mark.style.outline = `${strokeWidth} solid ${paint}`;
      if (!usesTextPaint && style.name === "underline") {
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
    return drawMarks();
  }

  let chain = (async () => {
    await renderSource();
    return drawMarks();
  })();

  return {
    ready: chain,
    get zoom() {
      return state.zoom;
    },
    update(locations, style) {
      chain = chain.then(() => {
        state.layers = Array.isArray(locations) || style !== undefined
          ? normalizeLayers({ locations, style: style ?? state.layers[0].style })
          : normalizeLayers(locations);
        return drawMarks();
      });
      return chain;
    },
    focus(id, focusOptions = {}) {
      chain = chain.then(async () => {
        if (focusOptions.zoom !== undefined) await setZoom(focusOptions.zoom);
        const marks = root.querySelectorAll(`.sh-mark[data-id="${CSS.escape(String(id))}"]`);
        if (!marks.length) return false;
        marks[0].scrollIntoView({ block: "center", inline: "center", behavior: focusOptions.behavior || "auto" });
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
