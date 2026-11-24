// Lens: one Reference, full page (V1 of the 2026-11-05 babble). The stored copy fills the view with the cited
// place marked and in the middle; the header carries the one answer (label, quote, status, both links); Details
// folds out with the status from `source-highlighter check`, the fallback chain (V5) and the Locations.
// Deep link: #ref=<id>. ‹ › and ← → walk the set. With no view server (a static folder, CONTRACT §7) it is lens.html,
// reading refs.json beside it or ?refs=<url> (and an optional ?state=<state.json>, ?pdfjs=<pdf.min.mjs>).

import { bytes, chain, drawRef, el, emptyState, links, loadSet, pill, remember, shownLevel, statusOf, storedUrl, draggable } from "./shared.js";

const base = new URL(".", import.meta.url);
const $ = (selector) => document.querySelector(selector);
const stage = $(".l-stage");
const details = $(".l-details");
const select = $(".l-select");
const styleSelect = $(".l-style");

const DETAILS_MIN = 240;
const DETAILS_MAX = 640;
// WHY a readable fixed zoom (not fit-to-width): the cited value should read at about its natural size, centred,
// without the reader zooming. Text fits its column.
const ZOOM = { html: 1, snapshot: 1, pdf: 1.5, vault: "fit" };

let set = null;
let current = null;
let view = null;
let level = null;
let style = remember("sh-lens:style") || "spotlight";
styleSelect.value = style;

// ---- Details: folds (remembered), and its boundary drags (remembered) ----

function applyFold() {
  const open = remember("sh-lens:details") === "open";
  document.body.classList.toggle("l-folded", !open);
  $(".l-details-toggle").setAttribute("aria-expanded", String(open));
}
$(".l-details-toggle").addEventListener("click", () => {
  remember("sh-lens:details", remember("sh-lens:details") === "open" ? "folded" : "open");
  applyFold();
});
applyFold();

function applyWidth(width) {
  details.style.width = `${Math.max(DETAILS_MIN, Math.min(DETAILS_MAX, width))}px`;
}
applyWidth(Number(remember("sh-lens:details-width")) || 340);
draggable($(".l-split"), {
  onMove(dx, _dy, starting, begin) {
    if (starting) return details.getBoundingClientRect().width;
    applyWidth(begin - dx); // the panel is on the right: dragging left widens it
    return begin;
  },
  onDone() {
    remember("sh-lens:details-width", String(Math.round(details.getBoundingClientRect().width)));
  },
});

// ---- one reference ----

function open(id, chosenLevel) {
  const ref = set.byId[id];
  if (!ref) return;
  current = id;
  const index = set.refs.indexOf(ref);
  select.value = id;
  $(".l-pos").textContent = `${index + 1} / ${set.refs.length}`;
  $(".l-prev").disabled = index === 0;
  $(".l-next").disabled = index === set.refs.length - 1;
  $(".l-label").textContent = ref.label || ref.id;
  const quote = $(".l-quote");
  quote.textContent = ref.quote || "";
  quote.hidden = !ref.quote;
  document.title = `${ref.label || ref.id} · Source Lens`;

  level = chosenLevel || shownLevel(ref, set.state);
  const status = statusOf(ref, set.state);
  $(".l-pill").replaceChildren(pill(status));
  $(".l-links").replaceChildren(links(ref, set.state, base, level));

  const statusBox = $(".l-status");
  statusBox.replaceChildren(pill(status), el("p", "l-why", status.why || ""));
  if (status.checkedAt) statusBox.append(el("p", "l-faint", `checked ${status.checkedAt.replace("T", " ").replace("Z", " UTC")}`));
  else statusBox.append(el("p", "l-faint", "Not checked yet: source-highlighter check refs.json --out statuses.json"));
  const both = $(".l-both");
  both.replaceChildren(bothLinks(ref, status));
  $(".l-where").textContent = ref.where || "";
  $(".l-where-section").hidden = !ref.where;
  drawChain(ref);

  view?.instance.destroy();
  view = null;
  $(".l-locations").replaceChildren();
  if (!level) {
    stage.replaceChildren(emptyState(ref, set.state, base));
    $(".l-locations").append(el("li", "l-faint", ref.locations?.length ? "nothing stored to find them on" : "none"));
  } else {
    view = drawRef(stage, ref, level, { base, state: set.state, pdfjs, style, zoom: ZOOM[level] });
    view.ready.then((result) => listLocations(result)).catch(() => {});
  }
  try {
    history.replaceState(null, "", `#ref=${encodeURIComponent(id)}`);
  } catch {
    // a sandboxed frame: the deep link just isn't updated
  }
}

function bothLinks(ref, status) {
  const list = el("ul", "l-linklist");
  if (ref.source.url) {
    const item = el("li");
    const live = el("a", "", status.liveLink || ref.source.url);
    live.href = status.liveLink || ref.source.url;
    live.target = "_blank";
    live.rel = "noopener noreferrer";
    item.append(el("span", "l-faint", "Live, highlighted by your browser · "), live);
    list.append(item);
  }
  for (const node of chain(ref, set.state, level)) {
    if (node.level === "live" || !node.kept) continue;
    const item = el("li");
    const stored = el("a", "", `stored ${node.level}${ref.source.retrieved_at ? ` · ${ref.source.retrieved_at.slice(0, 10)}` : ""}`);
    stored.href = storedUrl(ref, node.level, base, set.state);
    stored.target = "_blank";
    stored.rel = "noopener";
    item.append(el("span", "l-faint", "Stored copy · "), stored);
    list.append(item);
  }
  return list;
}

function drawChain(ref) {
  const list = $(".l-chain");
  list.replaceChildren();
  for (const node of chain(ref, set.state, level)) {
    const item = el("li", `l-node${node.kept ? " kept" : " not"}${node.shown ? " shown" : ""}`);
    item.dataset.level = node.level;
    const name = node.kept && node.drawable && !node.shown ? el("button", "l-node-pick", node.level) : el("b", "", node.level);
    if (name.tagName === "BUTTON") {
      name.type = "button";
      name.title = `Show the stored ${node.level}`;
      name.addEventListener("click", () => open(ref.id, node.level));
    }
    item.append(name, el("span", "", node.detail || ""), el("span", "l-faint", node.shown ? "shown" : node.when || ""));
    list.append(item);
  }
}

function listLocations(result) {
  const list = $(".l-locations");
  list.replaceChildren();
  const drawn = new Set(result.marks.map((mark) => String(mark.id)));
  for (const location of result.locations) {
    const kind = ["html", "box", "text", "note", "quote"].find((key) => location[key] !== undefined) || "page";
    const item = el("li");
    item.append(el("code", "", kind), el("span", "", ` ${location.label || location.id}`));
    const problem = result.unresolved.find((entry) => entry.location && String(entry.location.id) === String(location.id));
    if (problem) item.append(el("p", "l-problem", problem.reason));
    else if (drawn.has(String(location.id))) {
      const found = result.marks.find((mark) => String(mark.id) === String(location.id))?.found;
      if (found && !found.exact) item.append(el("p", "l-faint", `found approximately (${found.errors} edit${found.errors === 1 ? "" : "s"})`));
    }
    list.append(item);
  }
  if (!result.locations.length) list.append(el("li", "l-faint", "none for this copy"));
}

// ---- the set ----

const step = (delta) => {
  const next = set.refs[set.refs.findIndex((ref) => ref.id === current) + delta];
  if (next) open(next.id);
};
$(".l-prev").addEventListener("click", () => step(-1));
$(".l-next").addEventListener("click", () => step(1));
select.addEventListener("change", () => open(select.value));
styleSelect.addEventListener("change", () => {
  style = styleSelect.value;
  remember("sh-lens:style", style);
  open(current, level);
});
document.addEventListener("keydown", (event) => {
  if (event.target.closest("select, input, textarea") || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "ArrowLeft") step(-1);
  if (event.key === "ArrowRight") step(1);
});
const fromHash = () => decodeURIComponent((/ref=([^&]+)/.exec(location.hash) || [])[1] || "");
window.addEventListener("hashchange", () => {
  if (fromHash() && fromHash() !== current) open(fromHash());
});

const query = new URLSearchParams(location.search);
const pdfjs = query.get("pdfjs") ? new URL(query.get("pdfjs"), location.href).href : null;
// ?refs= means a static set (no view server): only what the link names is read.
set = await loadSet(base, query.get("refs") ? new URL(query.get("refs"), location.href) : new URL("refs.json", base), {
  stateUrl: query.get("state") ? new URL(query.get("state"), location.href) : null,
  probe: !query.get("refs"),
});
for (const [index, ref] of set.refs.entries()) {
  const option = el("option", "", `${index + 1}. ${ref.label || ref.id}`);
  option.value = ref.id;
  select.append(option);
}
open(set.byId[fromHash()] ? fromHash() : set.refs[0].id);
window.lens = { open, get current() { return current; }, get view() { return view; }, get level() { return level; }, bytes };
