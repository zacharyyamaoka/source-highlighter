// open.html: one stored source opened at a Location, from a plain URL, with no server (CONTRACT §7).
//
//   open.html?src=parts/jmc/drawing.pdf&where=page%3D1%26xywh%3Dpercent%3A18.3%2C76.3%2C6.3%2C2.6&quote=Rated%20torque
//
// It lands scrolled to the place with the mark drawn: a box for a page box, the quote's words for a quote, both when
// both. What cannot be placed is said in plain words ("not located: …") and nothing is marked for it — never a guessed
// box. WHY (Zach, 2026-11-24): "when I click that I want to basically be taken directly not just to the page, but see
// highlighted the exact text that you got that information from. That is how you create a fully linked datasheet all
// the way back to the source."

import { centerInScrollers, highlight, sourceKind } from "./source-highlighter.js";
import { asLocation } from "./source-msgs.js";

const $ = (selector) => document.querySelector(selector);
const body = document.body;
const stage = $(".o-stage");
const host = $(".o-host");
const details = $(".o-details");

const DETAILS_MIN = 240;
const DETAILS_MAX = 640;
// WHY a readable zoom band, not plain fit-to-width: the cited words should read at about their printed size the moment
// the page opens (on a phone a fitted A4 page is unreadable), and a huge window should not blow a page up to 3×.
const READABLE = { pdf: [1, 2], image: [0.75, 1.5], html: [0.75, 1], text: [1, 1.5], markdown: [1, 1.5] };
const PAGE_NOT_RENDERED = /^page \d+ is not rendered/;
const TEXTLESS = /no text layer/;

// ---- the request: query string, then the same keys in the hash (a fragment never reaches a server's logs) ----

function readParams() {
  const merged = new URLSearchParams(location.search);
  const hash = location.hash.replace(/^#/, "");
  if (/(^|&)(src|where|quote)=/.test(hash)) {
    for (const [key, value] of new URLSearchParams(hash)) {
      if (key === "where") merged.append(key, value);
      else merged.set(key, value);
    }
  }
  return merged;
}

const params = readParams();
const request = {
  src: params.get("src"),
  kind: params.get("kind"),
  where: params.getAll("where").filter((value) => value.trim()),
  quote: params.get("quote") || "",
  label: params.get("label") || "",
  url: params.get("url") || "",
  retrieved: params.get("retrieved") || "",
  sha256: (params.get("sha256") || "").toLowerCase(),
  page: params.get("page") ? Number(params.get("page")) : null,
  style: params.get("style") || "",
  zoom: params.get("zoom") || "",
  pdfjs: params.get("pdfjs") || document.querySelector('meta[name="source-highlighter:pdfjs"]')?.content || "pdfjs/pdf.min.mjs",
  embed: params.get("embed") === "1",
};

function remember(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {
    // storage blocked (a private window, a sandboxed frame): the page works, it just forgets
  }
  return null;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

const hostOf = (url) => {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
};
const day = (iso) => (iso ? String(iso).slice(0, 10) : "");
const fileName = (url) => {
  try {
    return decodeURIComponent(new URL(url, location.href).pathname.split("/").pop()) || url;
  } catch {
    return url;
  }
};

/**
 * The live link the browser highlights itself (CONTRACT §6), the same rule as `source-highlighter check`: a PDF opens
 * at its page with its quote searched (`#page=N&search="…"&phrase=true`, PDF open parameters + pdf.js), a web page at
 * its quote (a Text Fragment).
 */
// Python's urllib.parse.quote(value, safe=""): encodeURIComponent leaves !'()* alone, Python does not.
const percent = (value) => encodeURIComponent(value).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);

export function liveLink(url, kind, page, quote) {
  if (!url) return null;
  const bare = url.split("#")[0];
  if (kind === "pdf") {
    const parts = [];
    if (page) parts.push(`page=${page}`);
    if (quote && quote.trim()) parts.push(`search=${percent(`"${quote.trim()}"`)}`, "phrase=true");
    return parts.length ? `${bare}#${parts.join("&")}` : url;
  }
  if (kind === "html" && quote && quote.trim()) {
    const words = quote.trim().split(/\s+/);
    const encode = (term) => percent(term).replace(/-/g, "%2D");
    const directive = words.length === 1 ? encode(words[0]) : `${encode(words.slice(0, -1).join(" "))},${encode(words[words.length - 1])}`;
    return `${bare}#:~:text=${directive}`;
  }
  return url;
}

// ---- Details: folds (remembered) and its boundary drags (remembered) ----

function applyFold() {
  const open = remember("sh-open:details") === "open";
  body.classList.toggle("o-folded", !open);
  $(".o-details-toggle").setAttribute("aria-expanded", String(open));
}

function applyWidth(width) {
  details.style.width = `${Math.max(DETAILS_MIN, Math.min(DETAILS_MAX, width))}px`;
}

function draggable(handle, { onMove, onDone }) {
  // WHY pointer capture: the drag keeps going when the pointer leaves the thin handle.
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    document.documentElement.classList.add("o-dragging");
    const startX = event.clientX;
    const begin = onMove(0, true);
    const move = (moveEvent) => onMove(moveEvent.clientX - startX, false, begin);
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      document.documentElement.classList.remove("o-dragging");
      onDone?.();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
}

// ---- what to draw ----

/**
 * The request's places as module layers. A page box (or a whole page) is drawn as a box, text (a quote, an html range,
 * a markdown span) as a highlight, so a value that has both shows both. Anything that cannot address this kind of
 * source is kept as a problem to report, never dropped.
 */
function plan(kind, text) {
  const entries = []; // {id, given, location?, problem?}
  request.where.forEach((given, index) => {
    const id = `where-${index + 1}`;
    try {
      const value = given.trim().startsWith("{") ? JSON.parse(given) : given;
      entries.push({ id, given, location: { ...asLocation(value, kind, text), id, label: request.label || undefined } });
    } catch (error) {
      entries.push({ id, given, problem: error.name === "Unconvertible" ? error.message : `cannot read it: ${error.message}` });
    }
  });
  const pages = entries.map((entry) => entry.location?.page).filter((page) => Number.isInteger(page));
  if (request.quote.trim()) {
    const quote = request.quote.trim();
    const already = entries.some((entry) => {
      const location = entry.location || {};
      return location.quote === quote || location.html?.text_quote?.exact === quote;
    });
    if (kind === "image") {
      entries.push({ id: "quote", given: `quote=${quote}`, problem: "an image has no text to search; the quote is shown above, the box marks the place", quiet: true });
    } else if (!already) {
      const location = kind === "pdf" && pages.length ? { page: pages[0], quote } : { quote };
      entries.push({ id: "quote", given: `quote=${quote}`, location: { ...location, id: "quote", label: request.label || undefined } });
    }
  }
  const geometry = (location) => location.box !== undefined || (location.page !== undefined && location.quote === undefined);
  const forced = request.style || remember("sh-open:style") || "";
  const boxes = entries.filter((entry) => entry.location && geometry(entry.location)).map((entry) => entry.location);
  const words = entries.filter((entry) => entry.location && !geometry(entry.location)).map((entry) => entry.location);
  const layers = [];
  if (boxes.length) layers.push({ locations: boxes, style: forced || "box" });
  if (words.length) layers.push({ locations: words, style: forced || "highlight" });
  if (!layers.length) layers.push({ locations: [], style: "highlight" });
  return { entries, layers };
}

/** The pages a PDF needs drawn: the one cited page, or every page when the places are on several or unknown. */
function pagesToShow(entries) {
  if (request.page) return request.page;
  const located = entries.filter((entry) => entry.location);
  const pages = new Set(located.map((entry) => entry.location.page).filter((page) => Number.isInteger(page)));
  const everyOneHasAPage = located.length > 0 && located.every((entry) => Number.isInteger(entry.location.page));
  return pages.size === 1 && everyOneHasAPage ? [...pages][0] : null;
}

// ---- state ----

const state = {
  kind: null,
  text: null,
  entries: [],
  layers: [],
  view: null,
  shownPage: null, // a PDF drawn one page at a time: the page on screen (null: every page)
  citedPage: null,
  pageCount: null,
  first: null, // {marks, unresolved} of the cited view: what the status reports
  pin: null, // the stored copy's sha256 check: {ok, actual} | {unknown: reason}
};

function statusOf(result) {
  const resolvedIds = new Set(result.marks.map((mark) => String(mark.id)));
  const reasons = new Map(result.unresolved.filter((entry) => entry.location).map((entry) => [String(entry.location.id), entry.reason]));
  const reasonOf = (entry) => entry.problem || reasons.get(entry.id) || "not drawn";
  let counted = state.entries.filter((entry) => !entry.quiet);
  let located = counted.filter((entry) => entry.location && resolvedIds.has(entry.id));
  // A quote on a page with no text layer (a drawing, a scan) beside a box that marked the place: the words are shown in
  // the header, the box is the answer — the same as a quote on an image, so it is not counted as "not located".
  const textless = counted.filter((entry) => !located.includes(entry) && entry.location?.quote !== undefined && TEXTLESS.test(reasonOf(entry)));
  if (located.length && textless.length) {
    for (const entry of textless) entry.quiet = true;
    counted = counted.filter((entry) => !textless.includes(entry));
    located = counted.filter((entry) => entry.location && resolvedIds.has(entry.id));
  }
  const missing = counted.filter((entry) => !located.includes(entry));
  const approximate = result.marks.find((mark) => mark.found && !mark.found.exact);
  const pages = [...new Set(result.marks.map((mark) => mark.page))].sort((a, b) => a - b);
  const where = state.kind === "pdf" || state.kind === "image" ? (state.kind === "pdf" && pages.length ? ` on page ${pages.join(", ")}` : "") : "";
  if (!counted.length) {
    const quiet = state.entries.find((entry) => entry.quiet);
    return { tone: "quiet", word: "no-place", text: quiet ? "Quote shown; an image has no text to search" : "No place given: showing the source" };
  }
  if (!located.length) return { tone: "warn", word: "not-located", text: `Not located: ${reasonOf(missing[0])}`, why: missing.map(reasonOf) };
  if (missing.length) return { tone: "warn", word: "partly", text: `Marked${where} · ${missing.length} not located`, why: missing.map(reasonOf) };
  if (approximate) {
    const edits = approximate.found.errors;
    return { tone: "warn", word: "approximate", text: `Marked approximately${where} (${edits} edit${edits === 1 ? "" : "s"} from the quote)` };
  }
  return { tone: "ok", word: "marked", text: `Marked${where}` };
}

function showStatus(status) {
  const node = $(".o-status");
  node.className = `o-status o-${status.tone}`;
  node.dataset.status = status.word;
  node.replaceChildren(el("i"), el("span", "", status.text));
  node.title = status.why ? status.why.join("\n") : "";
  body.dataset.status = status.word;
}

function showLinks() {
  const links = $(".o-links");
  links.replaceChildren();
  const live = liveLink(request.url, state.kind, state.citedPage, request.quote || state.entries.find((entry) => entry.location?.quote)?.location.quote);
  if (live) {
    const a = el("a", "o-live", `live ↗ ${hostOf(request.url)}`);
    a.href = live;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = "The original, highlighted by your browser itself where it can (a Text Fragment, or #page=N&search= for a PDF)";
    links.append(a);
  }
  const stored = el("a", "o-stored", `stored ↗${request.retrieved ? ` ${day(request.retrieved)}` : ""}`);
  stored.href = new URL(request.src, location.href).href;
  stored.target = "_blank";
  stored.rel = "noopener";
  stored.title = "The stored copy this mark is drawn on";
  links.append(stored);
}

function listWhere(result) {
  const list = $(".o-where");
  list.replaceChildren();
  const byId = new Map();
  for (const mark of result.marks) if (!byId.has(String(mark.id))) byId.set(String(mark.id), mark);
  const reasons = new Map(result.unresolved.filter((entry) => entry.location).map((entry) => [String(entry.location.id), entry.reason]));
  for (const entry of state.entries) {
    const item = el("li");
    item.dataset.id = entry.id;
    item.append(el("code", "", entry.given));
    const mark = byId.get(entry.id);
    if (entry.quiet) item.append(el("p", "o-faint", entry.problem || `shown above, not searched: ${reasons.get(entry.id)}`));
    else if (mark) {
      const how = mark.found ? (mark.found.exact ? "the exact words" : `approximately (${mark.found.errors} edit${mark.found.errors === 1 ? "" : "s"}): “${mark.found.text}”`) : "";
      const page = state.kind === "pdf" ? `page ${mark.page}` : "";
      const button = el("button", "o-jump", `Marked${page ? ` on ${page}` : ""}${how ? ` · ${how}` : ""}`);
      button.type = "button";
      button.addEventListener("click", () => state.view?.focus(entry.id));
      item.append(button);
    } else {
      const reason = entry.problem || reasons.get(entry.id) || "not drawn";
      item.append(el("p", "o-problem", PAGE_NOT_RENDERED.test(reason) ? `on page ${entry.location.page} (go back to it)` : `not located: ${reason}`));
    }
    list.append(item);
  }
  if (!state.entries.length) list.append(el("li", "o-faint", "No place was given: add where=<selector> or quote=<the source's words> to the link."));
}

function showSource() {
  const list = $(".o-source");
  list.replaceChildren();
  const row = (term, value) => {
    if (value === null || value === undefined || value === "") return;
    list.append(el("dt", "", term));
    const dd = el("dd");
    if (value instanceof Node) dd.append(value);
    else dd.textContent = value;
    list.append(dd);
  };
  const stored = el("a", "", fileName(request.src));
  stored.href = new URL(request.src, location.href).href;
  stored.target = "_blank";
  stored.rel = "noopener";
  row("Stored copy", stored);
  row("Kind", state.kind + (state.pageCount && state.kind === "pdf" ? ` · ${state.pageCount} page${state.pageCount === 1 ? "" : "s"}` : ""));
  if (request.url) {
    const live = el("a", "", request.url);
    live.href = request.url;
    live.target = "_blank";
    live.rel = "noopener noreferrer";
    row("Original", live);
  }
  row("Retrieved", request.retrieved ? request.retrieved.replace("T", " ").replace("Z", " UTC") : "");
  if (state.pin) {
    if (state.pin.unknown) row("sha256", `not checked: ${state.pin.unknown}`);
    else if (state.pin.ok) row("sha256", `${state.pin.actual.slice(0, 16)}… matches the cited copy`);
    else {
      const node = el("span", "o-problem", `this copy is ${state.pin.actual.slice(0, 16)}…, the citation names ${request.sha256.slice(0, 16)}…: the stored bytes are not the cited ones`);
      row("sha256", node);
    }
  }
}

// ---- drawing ----

function readableZoom(current) {
  const [low, high] = READABLE[state.kind] || [1, 1.5];
  if (current < low) return low;
  if (current > high) return high;
  return null;
}

async function draw(page, { initial = false } = {}) {
  state.view?.destroy();
  state.shownPage = page;
  host.replaceChildren();
  body.dataset.ready = "";
  const zoomChoice = request.zoom || remember("sh-open:zoom") || "";
  const view = highlight(host, {
    source: state.text !== null ? { kind: state.kind, url: state.srcUrl, text: state.text } : { kind: state.kind, url: state.srcUrl },
    layers: state.layers,
    zoom: zoomChoice && zoomChoice !== "fit" ? Number(zoomChoice) : "fit",
    page: page ?? undefined,
    pdfjs: new URL(request.pdfjs, location.href).href,
  });
  state.view = view;
  let result = await view.ready;
  if (!zoomChoice) {
    const better = readableZoom(view.zoom);
    if (better !== null) {
      await view.focus(result.marks[0]?.id ?? "", { zoom: better });
      result = view.result;
    }
  }
  state.pageCount = view.pageCount ?? state.pageCount;
  if (initial || state.first === null) state.first = result;
  const first = host.querySelector(".sh-mark");
  if (first) {
    centerInScrollers(first);
    first.classList.add("sh-focus");
  }
  updatePages();
  if (!request.embed) {
    listWhere(result);
    showSource();
  }
  body.dataset.ready = "1";
  return result;
}

function updatePages() {
  const nav = $(".o-pages");
  const paged = state.kind === "pdf" && state.shownPage !== null && state.pageCount > 1;
  nav.hidden = !paged;
  if (!paged) return;
  $(".o-pos").textContent = `page ${state.shownPage} of ${state.pageCount}`;
  $(".o-prev").disabled = state.shownPage <= 1;
  $(".o-next").disabled = state.shownPage >= state.pageCount;
  const away = state.citedPage && state.shownPage !== state.citedPage;
  nav.classList.toggle("o-away", Boolean(away));
  nav.title = away ? `The cited place is on page ${state.citedPage}` : "";
}

async function checkPin() {
  if (!request.sha256) return;
  if (!globalThis.crypto?.subtle) {
    state.pin = { unknown: "this page cannot hash files (not a secure context)" };
    return;
  }
  try {
    const bytes = await (await fetch(state.srcUrl)).arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const actual = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    state.pin = { ok: actual === request.sha256, actual };
  } catch (error) {
    state.pin = { unknown: error.message };
  }
}

function emptyState(title, lines) {
  const box = el("div", "o-empty");
  const inner = el("div", "o-empty-in");
  inner.append(el("b", "", title));
  for (const line of lines) inner.append(typeof line === "string" ? el("p", "", line) : line);
  box.append(inner);
  host.replaceChildren(box);
}

async function main() {
  body.classList.toggle("o-embed", request.embed);
  $(".o-head").hidden = request.embed;
  if (!request.embed) {
    applyFold();
    applyWidth(Number(remember("sh-open:details-width")) || 340);
    $(".o-details-toggle").addEventListener("click", () => {
      remember("sh-open:details", remember("sh-open:details") === "open" ? "folded" : "open");
      applyFold();
    });
    draggable($(".o-split"), {
      onMove(dx, starting, begin) {
        if (starting) return details.getBoundingClientRect().width;
        applyWidth(begin - dx); // the panel is on the right: dragging left widens it
        return begin;
      },
      onDone() {
        remember("sh-open:details-width", String(Math.round(details.getBoundingClientRect().width)));
      },
    });
  }
  $(".o-label").textContent = request.label || (request.src ? fileName(request.src) : "Source");
  $(".o-quote").textContent = request.quote;
  $(".o-quote").hidden = !request.quote;
  document.title = `${request.label || (request.src ? fileName(request.src) : "Source")} · source`;

  if (!request.src) {
    showStatus({ tone: "quiet", word: "no-source", text: "Nothing to open" });
    emptyState("Nothing to open yet.", [
      "This page shows a stored source at the place a value was read from. Give it the file and the place:",
      el("code", "", "open.html?src=datasheet.pdf&where=page%3D2%26xywh%3Dpercent%3A61%2C40%2C7%2C2&quote=Rated%20torque"),
    ]);
    body.dataset.ready = "1";
    return;
  }

  state.kind = request.kind || sourceKind(request.src);
  try {
    state.srcUrl = new URL(request.src, location.href).href;
    if (state.kind === "text" || state.kind === "markdown") {
      const response = await fetch(state.srcUrl);
      if (!response.ok) throw new Error(`could not fetch ${state.srcUrl}: HTTP ${response.status}`);
      state.text = await response.text();
    }
    const { entries, layers } = plan(state.kind, state.text);
    state.entries = entries;
    state.layers = layers;
    const page = state.kind === "pdf" ? pagesToShow(entries) : null;
    state.citedPage = page;
    await checkPin();
    await draw(page, { initial: true });
    if (state.citedPage === null && state.first.marks.length) state.citedPage = state.first.marks[0].page;
    const status = statusOf(state.first);
    if (state.pin && state.pin.ok === false) {
      status.tone = "warn";
      status.text += " · the stored bytes are not the cited ones";
    }
    showStatus(status);
    if (!request.embed) {
      listWhere(state.first); // again: the status may have set a textless quote aside as shown, not searched
      showLinks();
    }
    if (request.embed) {
      const full = $(".o-open-full");
      const url = new URL(location.href);
      url.searchParams.delete("embed");
      full.href = url.href;
      full.hidden = false;
      full.dataset.tone = status.tone;
      full.textContent = status.tone === "ok" || status.tone === "quiet" ? "Open ↗" : `${status.text} · Open ↗`;
    }
  } catch (error) {
    showStatus({ tone: "bad", word: "error", text: `Can't open the stored copy: ${error.message}` });
    const lines = [`${fileName(request.src)} could not be shown (${error.message}).`];
    if (location.protocol === "file:") lines.push("A page opened from a file can read other files only when the browser allows it; serve the folder instead: python3 -m http.server --directory <folder>.");
    emptyState("Can't open the stored copy.", lines);
    if (!request.embed) showLinks();
    body.dataset.ready = "error";
  }
}

// ---- controls ----

$(".o-prev").addEventListener("click", () => state.shownPage > 1 && draw(state.shownPage - 1));
$(".o-next").addEventListener("click", () => state.shownPage < state.pageCount && draw(state.shownPage + 1));
const styleSelect = $(".o-style");
styleSelect.value = request.style || remember("sh-open:style") || "";
styleSelect.addEventListener("change", () => {
  remember("sh-open:style", styleSelect.value);
  const { layers } = plan(state.kind, state.text);
  state.layers = layers;
  draw(state.shownPage);
});
const zoomSelect = $(".o-zoom");
zoomSelect.value = request.zoom || remember("sh-open:zoom") || "";
zoomSelect.addEventListener("change", () => {
  remember("sh-open:zoom", zoomSelect.value);
  draw(state.shownPage);
});
document.addEventListener("keydown", (event) => {
  if (event.target.closest("select, input, textarea") || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "ArrowLeft" && !$(".o-pages").hidden) $(".o-prev").click();
  if (event.key === "ArrowRight" && !$(".o-pages").hidden) $(".o-next").click();
});

window.sourceOpen = { request, state, draw, liveLink };
await main();
