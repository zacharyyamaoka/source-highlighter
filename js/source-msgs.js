// source-msgs in the browser: the compact selector strings of PEP 0001 §6 (packages/source_msgs/selectors.py), read
// and turned into the CONTRACT §2 Locations source-highlighter.js draws.
//
//   import { parse, toLocation, asLocation } from "./source-msgs.js";
//   toLocation(parse("page=2&xywh=percent:61,40,7,2"), "pdf")   // → {page: 2, box: [0.61, 0.4, 0.68, 0.42]}
//   asLocation("text=Rated%20torque", "html")                   // → {html: {text_quote: {exact: "Rated torque"}}}
//
// WHY a port and not a new format: robot_models' `Stated.where` / `.anchor` and robotparts store these strings, and a
// static page (open.html) has no Python to read them. The Python module is the reference: tests/test_source_msgs_js.py
// runs both on the same corpus and requires the same selectors, the same Locations and the same refusals.
// Offsets are Unicode code points, as in CONTRACT §2 and Python; a JS string counts UTF-16 units, so they are converted.

export class Unconvertible extends Error {
  // This selector cannot address a source of this kind (or cannot be pinned in this source's text).
  constructor(message) {
    super(message);
    this.name = "Unconvertible";
  }
}

// Characters a compact value may hold as is, beside letters, digits and "_.-~" (Python's urllib.parse.quote keeps
// those always). WHY `,`, `&`, `=` are escaped: they are the grammar.
const SAFE = new Set("/[]()@*:'\"!$+;.~_#<>|^{}`-");
const EXTRA_KEYS = ["id", "label", "sha256"];
const KINDS = ["pdf", "image", "html", "text"];
const encoder = new TextEncoder();

/** Python's `urllib.parse.quote(value, safe=SAFE)`: UTF-8 bytes, upper-case hex, a space as %20. */
export function quoteValue(value) {
  let out = "";
  for (const byte of encoder.encode(String(value))) {
    const char = String.fromCharCode(byte);
    out += byte < 128 && (/[A-Za-z0-9]/.test(char) || SAFE.has(char)) ? char : `%${byte.toString(16).toUpperCase().padStart(2, "0")}`;
  }
  return out;
}

/** Python's `urllib.parse.unquote`: each run of %XX decoded as UTF-8 (bad bytes become U+FFFD), anything else kept. */
export function unquoteValue(value) {
  return String(value).replace(/(?:%[0-9A-Fa-f]{2})+/g, (run) => {
    const bytes = new Uint8Array(run.length / 3);
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = parseInt(run.slice(index * 3 + 1, index * 3 + 3), 16);
    return new TextDecoder("utf-8").decode(bytes);
  });
}

const codePoints = (text) => Array.from(text).length;

function checkKind(kind) {
  if (!KINDS.includes(kind)) throw new Error(`kind must be 'pdf', 'image', 'html' or 'text', got '${kind}'`);
}

function checkPage(page) {
  if (!Number.isInteger(page) || page < 1) throw new Error(`page must be an integer from 1 (pages are 1-based, as in PDF.js), got ${page}`);
}

function checkSha256(sha256) {
  if (sha256 !== undefined && sha256 !== null && !(typeof sha256 === "string" && /^[0-9a-f]{64}$/.test(sha256))) {
    throw new Error(`sha256 must be 64 lowercase hex digits (the file's sha256), got '${sha256}'`);
  }
}

const round9 = (value) => Math.round(value * 1e9) / 1e9;

function numberText(value) {
  const text = value.toFixed(7).replace(/0+$/, "").replace(/\.$/, "");
  return text === "" || text === "-0" ? "0" : text;
}

function extrasOf(selector) {
  const extras = {};
  for (const key of EXTRA_KEYS) if (selector[key] !== undefined && selector[key] !== null) extras[key] = selector[key];
  return extras;
}

const extrasCompact = (selector) => Object.entries(extrasOf(selector)).map(([key, value]) => `&${key}=${quoteValue(value)}`).join("");
const withExtras = (location, selector) => ({ ...location, ...extrasOf(selector) });

// ---- the selectors, as plain objects {type, ...fields} --------------------------------------------------------------

/** A selector checked as its Python constructor checks it. Returns a new object; throws on a bad field. */
export function makeSelector(type, fields) {
  const selector = { type, ...fields };
  for (const key of Object.keys(selector)) if (selector[key] === undefined) delete selector[key];
  if (type === "TextQuote") {
    if (typeof selector.exact !== "string" || !selector.exact.trim()) throw new Error("TextQuote needs the exact text");
    selector.prefix ??= "";
    selector.suffix ??= "";
    if (selector.page !== undefined && selector.page !== null) checkPage(selector.page);
  } else if (type === "TextPosition") {
    const { start, end } = selector;
    if (!(Number.isInteger(start) && Number.isInteger(end) && start >= 0 && start < end)) throw new Error(`TextPosition needs 0 <= start < end, got ${start}, ${end}`);
  } else if (type === "PageBox") {
    checkPage(selector.page);
    checkSha256(selector.sha256);
    for (const name of ["x", "y", "w", "h"]) {
      const value = selector[name];
      if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`PageBox.${name} must be a finite number, got ${value}`);
      selector[name] = round9(value);
    }
    const { x, y, w, h } = selector;
    if (w <= 0 || h <= 0 || x < 0 || y < 0 || x + w > 1 + 1e-9 || y + h > 1 + 1e-9) {
      throw new Error(`PageBox must lie on the page (0–1, w and h > 0), got x=${x} y=${y} w=${w} h=${h}`);
    }
  } else if (type === "Page") {
    checkPage(selector.page);
    checkSha256(selector.sha256);
  } else if (type === "XPath") {
    if (typeof selector.path !== "string" || !selector.path.trim()) throw new Error("XPath needs a path");
  } else if (type === "Css") {
    if (typeof selector.selector !== "string" || !selector.selector.trim()) throw new Error("Css needs a selector");
  } else if (type === "TableCell") {
    selector.table ??= 1;
    for (const name of ["row", "col", "table"]) {
      if (!Number.isInteger(selector[name]) || selector[name] < 1) throw new Error(`TableCell.${name} is 1-based, got ${selector[name]}`);
    }
  } else if (type === "Note") {
    if (!selector.heading && !selector.block) throw new Error("Note needs a heading or a block id");
  } else {
    throw new Error(`unknown selector type ${type}`);
  }
  return selector;
}

function htmlOnly(kind, what) {
  checkKind(kind);
  if (kind !== "html") throw new Unconvertible(`${what} addresses an HTML page's DOM, not ${kind}`);
}

/**
 * The CONTRACT §2 Location a selector makes on a source of `kind` ("pdf" | "image" | "html" | "text"); `text` is the
 * source's text, needed only to pin a quote by its context or find a markdown table cell. Throws Unconvertible with
 * the reason when the selector cannot address that kind: report it, never drop it.
 */
export function toLocation(selector, kind, text = null) {
  checkKind(kind);
  const article = kind === "image" ? "n" : "";
  switch (selector.type) {
    case "TextQuote": {
      if (kind === "image") throw new Unconvertible("an image has no text to find a quote in; give a PageBox");
      if (kind === "html") {
        const quote = { exact: selector.exact };
        if (selector.prefix) quote.prefix = selector.prefix;
        if (selector.suffix) quote.suffix = selector.suffix;
        return withExtras({ html: { text_quote: quote } }, selector);
      }
      if (kind === "text" && (selector.prefix || selector.suffix)) {
        // WHY pinned here: the {quote} Location carries no context, so a quote whose context makes it unique is turned
        // into the exact span — the same "exactly once, else report" rule as CONTRACT §2.
        if (text === null || text === undefined) throw new Unconvertible("pinning a quote by its prefix/suffix needs the source's text");
        const needle = selector.prefix + selector.exact + selector.suffix;
        const starts = [];
        // non-overlapping, as Python's re.finditer
        for (let at = text.indexOf(needle); at !== -1; at = text.indexOf(needle, at + needle.length)) starts.push(at);
        if (!starts.length) throw new Unconvertible(`"${selector.exact}" with that prefix/suffix is not in the source`);
        if (starts.length > 1) throw new Unconvertible(`ambiguous: "${selector.exact}" with that prefix/suffix occurs ${starts.length} times`);
        const start = codePoints(text.slice(0, starts[0])) + codePoints(selector.prefix);
        return withExtras({ text: { start, end: start + codePoints(selector.exact) } }, selector);
      }
      const location = selector.page === undefined || selector.page === null ? { quote: selector.exact } : { page: selector.page, quote: selector.exact };
      return withExtras(location, selector);
    }
    case "TextPosition": {
      const span = { start: selector.start, end: selector.end };
      if (kind === "text") return withExtras({ text: span }, selector);
      if (kind === "html") return withExtras({ html: { source_range: span } }, selector);
      throw new Unconvertible(`a text position counts into text; a${article} ${kind} needs a PageBox`);
    }
    case "PageBox": {
      if (kind === "pdf" || kind === "image") {
        const box = [selector.x, selector.y, round9(selector.x + selector.w), round9(selector.y + selector.h)];
        return withExtras({ page: selector.page, box }, selector);
      }
      throw new Unconvertible(
        `a page box addresses a PDF page or an image, not ${kind}` +
          (kind === "html" ? "; an HTML source draws it on its stored snapshot when the capture kept one" : ""),
      );
    }
    case "Page":
      if (kind === "pdf" || kind === "image") return withExtras({ page: selector.page }, selector);
      throw new Unconvertible(`a page addresses a PDF or an image, not ${kind}`);
    case "XPath":
      htmlOnly(kind, "an XPath");
      return withExtras({ html: { xpath: selector.path } }, selector);
    case "Css":
      htmlOnly(kind, "a CSS selector");
      return withExtras({ html: { css: selector.selector } }, selector);
    case "TableCell": {
      if (kind === "html") {
        // WHY descendant::tr: it counts rows through <thead>/<tbody> the parser inserts, as a reader would.
        const xpath = `(//table)[${selector.table}]/descendant::tr[${selector.row}]/*[self::td or self::th][${selector.col}]`;
        return withExtras({ html: { xpath } }, selector);
      }
      if (kind === "text") {
        if (text === null || text === undefined) throw new Unconvertible("a table cell in markdown needs the source's text");
        const [start, end] = gfmTableCell(text, selector.table, selector.row, selector.col);
        return withExtras({ text: { start, end } }, selector);
      }
      throw new Unconvertible(
        `a table cell on a${article} ${kind} needs its box: markdownify's index.json gives every table_cell block a page + box (use a PageBox)`,
      );
    }
    case "Note": {
      if (kind !== "text") throw new Unconvertible(`a note heading/block addresses a markdown note, not ${kind}`);
      const note = {};
      if (selector.heading) note.heading = selector.heading;
      if (selector.block) note.block = selector.block;
      return withExtras({ note }, selector);
    }
    default:
      throw new Error(`unknown selector type ${selector.type}`);
  }
}

/** A selector's one-line compact string (the inverse of parse). */
export function compact(selector) {
  switch (selector.type) {
    case "TextQuote": {
      const parts = (selector.prefix ? [`${quoteValue(selector.prefix)}-`] : []).concat([quoteValue(selector.exact)]);
      if (selector.suffix) parts.push(`-${quoteValue(selector.suffix)}`);
      const page = selector.page !== undefined && selector.page !== null ? `page=${selector.page}&` : "";
      return `${page}text=${parts.join(",")}${extrasCompact(selector)}`;
    }
    case "TextPosition":
      return `char=${selector.start},${selector.end}${extrasCompact(selector)}`;
    case "PageBox":
      return `page=${selector.page}&xywh=percent:${[selector.x, selector.y, selector.w, selector.h].map((value) => numberText(value * 100)).join(",")}${extrasCompact(selector)}`;
    case "Page":
      return `page=${selector.page}${extrasCompact(selector)}`;
    case "XPath":
      return `xpath=${quoteValue(selector.path)}${extrasCompact(selector)}`;
    case "Css":
      return `css=${quoteValue(selector.selector)}${extrasCompact(selector)}`;
    case "TableCell":
      return `table=${selector.table}&row=${selector.row}&col=${selector.col}${extrasCompact(selector)}`;
    case "Note":
      return ["heading", "block"].filter((key) => selector[key]).map((key) => `${key}=${quoteValue(selector[key])}`).join("&") + extrasCompact(selector);
    default:
      throw new Error(`unknown selector type ${selector.type}`);
  }
}

// ---- markdown tables ----------------------------------------------------------------------------------------------

const DELIMITER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

/** Each cell's stripped content as [start, end) code-point offsets into the whole text. `\|` does not split. */
function cells(line, lineStart) {
  const chars = Array.from(line);
  let bounds = [];
  chars.forEach((char, index) => {
    if (char === "|" && (index === 0 || chars[index - 1] !== "\\")) bounds.push(index);
  });
  const stripped = line.trim();
  const rightEnd = Array.from(line.trimEnd()).length;
  if (!stripped.startsWith("|")) bounds = [-1, ...bounds];
  if (!stripped.endsWith("|") || (bounds.length && bounds[bounds.length - 1] !== rightEnd - 1)) bounds = [...bounds, rightEnd];
  const found = [];
  for (let index = 0; index + 1 < bounds.length; index += 1) {
    let start = bounds[index] + 1;
    let end = bounds[index + 1];
    while (start < end && /\s/u.test(chars[start])) start += 1;
    while (end > start && /\s/u.test(chars[end - 1])) end -= 1;
    found.push([lineStart + start, lineStart + end]);
  }
  return found;
}

/** The code-point span of a GFM pipe-table cell's content (1-based table/row/col; row 1 = header row). */
export function gfmTableCell(text, table, row, col) {
  const lines = text.split("\n");
  const offsets = [0];
  for (const line of lines) offsets.push(offsets[offsets.length - 1] + codePoints(line) + 1);
  let found = 0;
  let index = 0;
  while (index < lines.length - 1) {
    if (lines[index].includes("|") && DELIMITER.test(lines[index + 1]) && lines[index + 1].includes("-")) {
      found += 1;
      const rows = [index];
      let cursor = index + 2;
      while (cursor < lines.length && lines[cursor].trim() && lines[cursor].includes("|")) {
        rows.push(cursor);
        cursor += 1;
      }
      if (found === table) {
        if (row > rows.length) throw new Unconvertible(`table ${table} has ${rows.length} rows; there is no row ${row}`);
        const rowCells = cells(lines[rows[row - 1]], offsets[rows[row - 1]]);
        if (col > rowCells.length) throw new Unconvertible(`row ${row} of table ${table} has ${rowCells.length} cells; there is no column ${col}`);
        const [start, end] = rowCells[col - 1];
        if (start === end) throw new Unconvertible(`cell row ${row}, column ${col} of table ${table} is empty`);
        return [start, end];
      }
      index = cursor;
      continue;
    }
    index += 1;
  }
  throw new Unconvertible(`the source has ${found} markdown table${found !== 1 ? "s" : ""}; there is no table ${table}`);
}

// ---- compact strings back to selectors -----------------------------------------------------------------------------

function wholeNumber(value, name) {
  if (!/^\d+$/.test(value)) throw new Error(`${name} must be a whole number, got '${value}'`);
  return Number(value);
}

/** A compact string (any selector's compact()) back to its selector, {type, ...fields}. Throws on a bad string. */
export function parse(text) {
  if (typeof text !== "string" || !text.trim()) throw new Error("an empty compact location");
  const pairs = new Map();
  for (const part of text.trim().replace(/^#+/, "").split("&")) {
    const at = part.indexOf("=");
    const key = at === -1 ? part : part.slice(0, at);
    if (at === -1 || pairs.has(key)) throw new Error(`cannot read '${text}': expected key=value pairs joined by &`);
    pairs.set(key, part.slice(at + 1));
  }
  const extras = {};
  for (const key of ["id", "label"]) {
    if (pairs.has(key)) {
      extras[key] = unquoteValue(pairs.get(key));
      pairs.delete(key);
    }
  }
  const pin = {};
  if (pairs.has("sha256")) {
    pin.sha256 = pairs.get("sha256");
    pairs.delete("sha256");
  }
  const keys = [...pairs.keys()].sort().join(",");
  const only = (...names) => [...pairs.keys()].every((key) => names.includes(key));
  if (pin.sha256 !== undefined && keys !== "page,xywh" && keys !== "page") {
    throw new Error(`cannot read '${text}': sha256= pins a geometry selector (page, page + xywh) only`);
  }
  if (pairs.has("text") && only("text", "page")) {
    const tokens = pairs.get("text").split(","); // split before unquoting: an escaped %2C is part of the text
    let prefix = "";
    let suffix = "";
    if (tokens.length > 1 && tokens[0].endsWith("-")) prefix = unquoteValue(tokens.shift().slice(0, -1));
    if (tokens.length > 1 && tokens[tokens.length - 1].startsWith("-")) suffix = unquoteValue(tokens.pop().slice(1));
    if (tokens.length !== 1) throw new Error(`cannot read '${text}': a text= quote holds one exact text (a start,end range is not supported)`);
    const page = pairs.has("page") ? wholeNumber(pairs.get("page"), "page") : undefined;
    return makeSelector("TextQuote", { exact: unquoteValue(tokens[0]), prefix, suffix, page, ...extras });
  }
  if (keys === "char") {
    const match = /^(\d+),(\d+)$/.exec(pairs.get("char"));
    if (!match) throw new Error(`cannot read '${text}': char= needs start,end`);
    return makeSelector("TextPosition", { start: Number(match[1]), end: Number(match[2]), ...extras });
  }
  if (keys === "page,xywh") {
    const match = /^percent:([\d.]+),([\d.]+),([\d.]+),([\d.]+)$/.exec(pairs.get("xywh"));
    if (!match) throw new Error(`cannot read '${text}': xywh= needs percent:x,y,w,h`);
    const [x, y, w, h] = match.slice(1).map((value) => {
      if (!/^(\d+\.?\d*|\.\d+)$/.test(value)) throw new Error(`could not convert string to float: '${value}'`);
      return Number(value) / 100;
    });
    return makeSelector("PageBox", { page: wholeNumber(pairs.get("page"), "page"), x, y, w, h, ...extras, ...pin });
  }
  if (keys === "page") return makeSelector("Page", { page: wholeNumber(pairs.get("page"), "page"), ...extras, ...pin });
  if (keys === "xpath") return makeSelector("XPath", { path: unquoteValue(pairs.get("xpath")), ...extras });
  if (keys === "css") return makeSelector("Css", { selector: unquoteValue(pairs.get("css")), ...extras });
  if (keys === "col,row,table") {
    return makeSelector("TableCell", {
      row: wholeNumber(pairs.get("row"), "row"),
      col: wholeNumber(pairs.get("col"), "col"),
      table: wholeNumber(pairs.get("table"), "table"),
      ...extras,
    });
  }
  if (pairs.size && only("heading", "block")) {
    const note = {};
    for (const [key, value] of pairs) note[key] = unquoteValue(value);
    return makeSelector("Note", { ...note, ...extras });
  }
  throw new Error(`cannot read '${text}': unknown keys ${[...pairs.keys()].sort().join(", ")}`);
}

/**
 * Anything a caller may hand over as a location → the CONTRACT §2 Location for a source of `kind`: a compact string
 * (parsed, then converted), a selector object ({type, …}), or a §2 dict (kept as it is: an html Location may carry
 * several selectors — source_range + xpath + text_quote — which no single compact string can). `kind` may also be
 * the module's "markdown" (a rendered note counts into the same text as "text").
 */
export function asLocation(value, kind, text = null) {
  const msgKind = kind === "markdown" ? "text" : kind;
  if (typeof value === "string") return toLocation(parse(value), msgKind, text);
  if (value && typeof value === "object" && typeof value.type === "string") return toLocation(value, msgKind, text);
  if (value && typeof value === "object") return value;
  throw new Error(`a location is a compact string, a selector or a CONTRACT §2 dict, got ${typeof value}`);
}
