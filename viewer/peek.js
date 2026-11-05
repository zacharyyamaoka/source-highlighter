// Peek: citations in any page (V4 of the 2026-11-05 babble).
//
//   <script type="module" src="http://127.0.0.1:PORT/peek.js" data-refs="refs.json"></script>
//   …rated at <span data-ref="ref-1">9 N·m</span>
//
// Hovering a citation shows a crop of the stored copy at the mark, with its status and both links; clicking pins a
// panel (moved by its header, resized by its corner, both remembered) with the whole source; "Open full" goes to
// Lens. The host page is left alone except a dotted underline and a superscript. data-refs resolves against the
// host page; the stored files and statuses come from the viewer server peek.js was loaded from.

import { drawRef, el, emptyState, links, loadSet, pill, remember, shownLevel, statusOf, draggable } from "./shared.js";

const base = new URL(".", import.meta.url);
const script = [...document.querySelectorAll("script[src]")].find((node) => new URL(node.src, document.baseURI).href === import.meta.url)
  || document.querySelector("script[data-refs]");
const refsUrl = script?.dataset.refs ? new URL(script.dataset.refs, document.baseURI) : new URL("refs.json", base);

const PANEL_KEY = "sh-peek:panel";
const PANEL_MIN = { w: 320, h: 240 };
const CROP_ZOOM = { html: 0.75, snapshot: 0.75, pdf: 1.25, vault: "fit" };
const PIN_ZOOM = { html: 0.9, snapshot: 0.9, pdf: 1.25, vault: "fit" };
const HOVER_DELAY_MS = 120;
const HIDE_DELAY_MS = 200;

for (const name of ["common.css", "peek.css"]) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL(name, base).href;
  document.head.append(link);
}

const set = await loadSet(base, refsUrl);
const number = (id) => set.refs.findIndex((ref) => ref.id === id) + 1;

let card = null;
let cardView = null;
let cardFor = null;
let showTimer = null;
let hideTimer = null;
let pin = null;
let pinView = null;
let pinned = null;

// ---- citations ----

function wire(cite) {
  if (cite.classList.contains("shp-cite")) return;
  const id = cite.dataset.ref;
  cite.classList.add("shp-cite");
  if (!set.byId[id]) {
    cite.classList.add("shp-missing");
    cite.title = `No reference "${id}" in ${refsUrl.pathname.split("/").pop()}`;
    return;
  }
  cite.tabIndex = 0;
  cite.setAttribute("role", "button");
  cite.setAttribute("aria-label", `${cite.textContent.trim()} — source: ${set.byId[id].label || id}`);
  const sup = el("sup", "shp-n", String(number(id)));
  sup.setAttribute("aria-hidden", "true");
  cite.append(sup);
  const enter = () => {
    clearTimeout(hideTimer);
    if (pinned === id) return;
    clearTimeout(showTimer);
    showTimer = setTimeout(() => peek(id, cite), HOVER_DELAY_MS);
  };
  const leave = () => {
    clearTimeout(showTimer);
    hideTimer = setTimeout(hideCard, HIDE_DELAY_MS);
  };
  cite.addEventListener("mouseenter", enter);
  cite.addEventListener("focus", enter);
  cite.addEventListener("mouseleave", leave);
  cite.addEventListener("blur", leave);
  cite.addEventListener("click", () => pinRef(id));
  cite.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      pinRef(id);
    }
  });
}

// ---- hover: a crop at the mark ----

function peek(id, anchor) {
  const ref = set.byId[id];
  if (!ref || pinned === id) return null;
  hideCard();
  cardFor = id;
  card = el("div", "shp-card");
  card.setAttribute("role", "dialog");
  card.dataset.ref = id;
  const title = el("div", "shp-title");
  title.append(el("span", "shp-num", String(number(id))), el("b", "", ref.label || id));
  const crop = el("div", "shp-crop");
  const foot = el("div", "shp-foot");
  const level = shownLevel(ref, set.state);
  foot.append(pill(statusOf(ref, set.state)), links(ref, set.state, base, level));
  card.append(title, crop, foot, el("div", "shp-hint", "Click to pin the whole source"));
  card.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  card.addEventListener("mouseleave", () => {
    hideTimer = setTimeout(hideCard, HIDE_DELAY_MS);
  });
  document.body.append(card);
  place(card, anchor);
  if (level) cardView = drawRef(crop, ref, level, { base, style: "highlight", zoom: CROP_ZOOM[level], onlyPage: true });
  else crop.append(emptyState(ref, set.state, base));
  return cardView?.ready;
}

function place(node, anchor) {
  const at = anchor.getBoundingClientRect();
  const { offsetWidth: width, offsetHeight: height } = node;
  node.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, at.left))}px`;
  node.style.top = `${at.bottom + height + 8 < innerHeight ? at.bottom + 6 : Math.max(8, at.top - height - 6)}px`;
}

function hideCard() {
  cardView?.instance.destroy();
  cardView = null;
  card?.remove();
  card = null;
  cardFor = null;
}

// ---- click: a pinned, movable, resizable panel ----

function geometry() {
  let saved = null;
  try {
    saved = JSON.parse(remember(PANEL_KEY) || "null");
  } catch {
    saved = null;
  }
  const g = saved || { w: 520, h: Math.min(620, innerHeight - 80), x: innerWidth - 540, y: 40 };
  const w = Math.max(PANEL_MIN.w, Math.min(innerWidth - 16, g.w));
  const h = Math.max(PANEL_MIN.h, Math.min(innerHeight - 16, g.h));
  return { w, h, x: Math.max(8, Math.min(innerWidth - w - 8, g.x)), y: Math.max(8, Math.min(innerHeight - h - 8, g.y)) };
}

function applyGeometry(g) {
  Object.assign(pin.style, { left: `${g.x}px`, top: `${g.y}px`, width: `${g.w}px`, height: `${g.h}px` });
}

function saveGeometry() {
  const r = pin.getBoundingClientRect();
  remember(PANEL_KEY, JSON.stringify({ x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }));
}

function buildPin() {
  pin = el("div", "shp-pin");
  pin.setAttribute("role", "dialog");
  const head = el("div", "shp-pin-head");
  const full = el("a", "shp-full", "Open full ↗");
  full.target = "_blank";
  full.rel = "noopener";
  const close = el("button", "shp-close", "✕");
  close.type = "button";
  close.title = "Close (Esc)";
  close.addEventListener("click", unpin);
  head.append(el("span", "shp-num"), el("b"), full, close);
  const resize = el("div", "shp-resize");
  resize.title = "Drag to resize";
  pin.append(head, el("div", "shp-pin-meta"), el("div", "shp-pin-body"), resize);
  document.body.append(pin);
  draggable(head, {
    onMove(dx, dy, starting, begin) {
      if (starting) return pin.getBoundingClientRect();
      Object.assign(pin.style, { left: `${begin.left + dx}px`, top: `${begin.top + dy}px` });
      return begin;
    },
    onDone: saveGeometry,
  });
  draggable(resize, {
    onMove(dx, dy, starting, begin) {
      if (starting) return pin.getBoundingClientRect();
      Object.assign(pin.style, { width: `${Math.max(PANEL_MIN.w, begin.width + dx)}px`, height: `${Math.max(PANEL_MIN.h, begin.height + dy)}px` });
      return begin;
    },
    onDone: saveGeometry,
  });
}

function pinRef(id) {
  const ref = set.byId[id];
  if (!ref) return null;
  clearTimeout(showTimer); // the hover that led to this click must not open a card over the pinned panel
  hideCard();
  if (!pin) buildPin();
  pinned = id;
  pin.hidden = false;
  pin.dataset.ref = id;
  applyGeometry(geometry());
  for (const cite of document.querySelectorAll(".shp-cite")) cite.classList.toggle("shp-on", cite.dataset.ref === id);
  pin.querySelector(".shp-num").textContent = String(number(id));
  pin.querySelector(".shp-pin-head b").textContent = ref.label || id;
  pin.querySelector(".shp-full").href = new URL(`#ref=${encodeURIComponent(id)}`, base).href;
  const level = shownLevel(ref, set.state);
  pin.querySelector(".shp-pin-meta").replaceChildren(pill(statusOf(ref, set.state)), links(ref, set.state, base, level));
  pinView?.instance.destroy();
  pinView = null;
  const body = pin.querySelector(".shp-pin-body");
  if (level) pinView = drawRef(body, ref, level, { base, style: "highlight", zoom: PIN_ZOOM[level] });
  else body.replaceChildren(emptyState(ref, set.state, base));
  return pinView?.ready;
}

function unpin() {
  pinView?.instance.destroy();
  pinView = null;
  pinned = null;
  if (pin) pin.hidden = true;
  for (const cite of document.querySelectorAll(".shp-cite.shp-on")) cite.classList.remove("shp-on");
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    hideCard();
    unpin();
  }
});
addEventListener("resize", () => {
  if (pin && !pin.hidden) applyGeometry(geometry());
});

// Citations already in the page, and any the host adds later (a CRM that renders notes after load).
for (const cite of document.querySelectorAll("[data-ref]")) wire(cite);
new MutationObserver((records) => {
  for (const record of records) {
    for (const node of record.addedNodes) {
      if (node.nodeType !== Node.ELEMENT_NODE || node.closest(".shp-card, .shp-pin")) continue;
      if (node.matches("[data-ref]")) wire(node);
      for (const cite of node.querySelectorAll("[data-ref]")) wire(cite);
    }
  }
}).observe(document.body, { childList: true, subtree: true });

window.sourcePeek = {
  peek: (id) => peek(id, document.querySelector(`[data-ref="${CSS.escape(id)}"]`)),
  pin: pinRef,
  unpin,
  get card() {
    return card;
  },
  get cardFor() {
    return cardFor;
  },
  get pinned() {
    return pinned;
  },
};
document.dispatchEvent(new CustomEvent("source-peek:ready"));
