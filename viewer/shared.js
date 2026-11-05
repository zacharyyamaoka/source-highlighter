// What Lens (the full page) and Peek (the embeddable citation) share: loading a reference set from the viewer
// server, the §6 status words, which stored copy to show and with which Locations, and the small pieces both draw
// the same way (status pill, both links, the fallback chain).
//
// WHY one stored copy, never the live page (CONTRACT §6): a cross-origin page can't be marked in place, and a
// re-render could drift from what was cited. The live side is a link the browser highlights itself.

import { centerInScrollers, highlight } from "./source-highlighter.js";

export const LEVELS = ["html", "snapshot", "archive", "pdf"];
// archive (WACZ) needs a replay viewer: kept and listed, never drawn here.
const DRAWABLE = new Set(["html", "snapshot", "pdf", "vault"]);

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

export function remember(key, value) {
  try {
    if (value === undefined) return window.localStorage.getItem(key);
    window.localStorage.setItem(key, value);
  } catch {
    // storage blocked (a private window): the page still works, it just forgets
  }
  return null;
}

export function bytes(count) {
  if (count === null || count === undefined) return "";
  if (count < 1024) return `${count} B`;
  if (count < 1024 * 1024) return `${(count / 1024).toFixed(count < 10240 ? 1 : 0)} KB`;
  return `${(count / 1024 / 1024).toFixed(1)} MB`;
}

const day = (iso) => (iso ? String(iso).slice(0, 10) : "");
const hostOf = (url) => {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/** The reference set and the server's state ({files, statuses, checked_at, vault, pdfjs}). */
export async function loadSet(base, refsUrl) {
  const [refs, state] = await Promise.all([
    fetch(refsUrl).then((response) => (response.ok ? response.json() : Promise.reject(new Error(`${refsUrl}: HTTP ${response.status}`)))),
    fetch(new URL("api/state", base)).then((response) => response.json()),
  ]);
  return { refs, state, byId: Object.fromEntries(refs.map((ref) => [ref.id, ref])) };
}

/**
 * The status to show: {word, tone, label, why, checkedAt, liveLink}. tone is "ok" | "warn" | "bad" | "quiet".
 * WHY amber and red only for deviated and dead: everything else is calm — a not-yet-dead "unreachable (1 of 3)"
 * and an unchecked reference are not alarms.
 */
export function statusOf(ref, state) {
  const checked = state.statuses?.[ref.id];
  const stored = Boolean(ref.source.vault) || kept(ref, state).some((level) => DRAWABLE.has(level));
  if (!checked) {
    if (!stored) return { word: "link-only", tone: "quiet", label: "nothing stored; can’t show where", why: "nothing was kept of this source", checkedAt: null, liveLink: ref.source.url || null };
    return { word: "unchecked", tone: "quiet", label: ref.source.vault ? "vault note" : "not checked", why: "run source-highlighter check to compare it with the live source", checkedAt: null, liveLink: ref.source.url || null };
  }
  const tone = { live: "ok", deviated: "warn", dead: "bad" }[checked.status] || "quiet";
  return { word: checked.status, tone, label: checked.label, why: checked.why, checkedAt: checked.checked_at, liveLink: checked.live_link || ref.source.url || null };
}

/** Stored levels whose files exist, in fallback order. */
export function kept(ref, state) {
  const files = state.files?.[ref.id] || {};
  return [...LEVELS, "vault"].filter((level) => files[level]?.kept);
}

/** The level §6 shows: the vault note, else html → snapshot → pdf (archive can't be drawn here). Or null. */
export function shownLevel(ref, state) {
  return kept(ref, state).find((level) => DRAWABLE.has(level)) || null;
}

/**
 * The fallback chain (V5's strip): live, then each stored level — kept or not, its size, and which one is shown.
 */
export function chain(ref, state, showing) {
  const files = state.files?.[ref.id] || {};
  const status = statusOf(ref, state);
  const nodes = [];
  if (!ref.source.vault) nodes.push({ level: "live", kept: Boolean(ref.source.url), detail: status.word === "unchecked" ? "not checked" : status.label, when: day(status.checkedAt) && `checked ${day(status.checkedAt)}` });
  const levels = ref.source.vault ? ["vault"] : LEVELS;
  for (const level of levels) {
    const file = files[level];
    nodes.push({
      level,
      kept: Boolean(file?.kept),
      bytes: file?.bytes ?? null,
      drawable: DRAWABLE.has(level),
      shown: level === showing,
      detail: file?.kept ? (DRAWABLE.has(level) ? bytes(file.bytes) : `${bytes(file.bytes)} · needs a replay viewer`) : file ? "missing on disk" : "not kept",
      when: file?.kept ? (level === "vault" ? "the vault is the copy" : `stored ${day(ref.source.retrieved_at)}`) : "",
    });
  }
  return nodes;
}

/**
 * The module's source and Locations for one reference at one stored level. Locations of other kinds belong to
 * other levels (a reference may carry html Locations for its page and boxes for its screenshot). A reference with
 * none for this level, but a quote, is found by its quote (§5 quote-only).
 */
export function sourceFor(ref, level, base) {
  const url = new URL(`stored/${encodeURIComponent(ref.id)}/${level}`, base).href;
  const all = (ref.locations || []).map((location, index) => ({ ...location, id: location.id ?? `loc-${index + 1}` }));
  const isKind = (location, ...keys) => keys.some((key) => location[key] !== undefined);
  let source;
  let locations;
  if (level === "html") {
    source = { kind: "html", url };
    locations = all.filter((location) => isKind(location, "html"));
  } else if (level === "snapshot") {
    source = { kind: "image", url };
    locations = all.filter((location) => isKind(location, "box") || (location.page !== undefined && !isKind(location, "quote", "html", "text", "note")));
  } else if (level === "pdf") {
    source = { kind: "pdf", url };
    locations = all.filter((location) => isKind(location, "box") || (location.page !== undefined && !isKind(location, "html", "text", "note")));
  } else {
    source = { kind: "text", url };
    locations = all.filter((location) => isKind(location, "note", "text"));
  }
  if (!locations.length && ref.quote && level !== "snapshot") locations = [{ id: "quote", label: ref.quote, quote: ref.quote }];
  return { source, locations };
}

/**
 * Draw `ref` at `level` into `host` (a scroll container) and bring the first mark to its middle.
 * Returns {instance, ready: Promise<{marks, unresolved}>}.
 */
export function drawRef(host, ref, level, { base, style = "highlight", zoom = "fit", onlyPage = false } = {}) {
  host.replaceChildren();
  delete host.dataset.ready;
  const { source, locations } = sourceFor(ref, level, base);
  const page = onlyPage ? locations.find((location) => Number.isInteger(location.page))?.page : undefined;
  const instance = highlight(host, {
    source,
    locations,
    style,
    zoom,
    page,
    pdfjs: new URL("pdfjs/pdf.min.mjs", base).href,
  });
  const ready = instance.ready.then((result) => {
    const first = host.querySelector(".sh-mark");
    if (first) centerInScrollers(first);
    host.dataset.ready = "1";
    return { ...result, locations };
  });
  ready.catch((error) => {
    host.replaceChildren(el("div", "sh-v-error", `Could not show the stored ${level}: ${error.message}`));
  });
  return { instance, ready };
}

/** The status pill: the one answer, first. */
export function pill(status) {
  const node = el("span", `sh-v-pill sh-v-${status.tone}`);
  node.dataset.status = status.word;
  node.append(el("i"), el("span", "", status.label));
  node.title = status.why || "";
  return node;
}

/** Both links, always (Robust Links): the live URL the browser highlights, and the dated stored copy. */
export function links(ref, state, base, showing) {
  const node = el("span", "sh-v-links");
  const status = statusOf(ref, state);
  if (ref.source.vault) {
    node.append(el("span", "sh-v-muted", `vault · ${ref.source.vault}`));
  } else if (ref.source.url) {
    const live = el("a", "sh-v-live", `live ↗ ${hostOf(ref.source.url)}`);
    live.href = status.liveLink || ref.source.url;
    live.target = "_blank";
    live.rel = "noopener noreferrer";
    live.title = "The live page, highlighted by your browser (Text Fragment / #page=N)";
    node.append(live);
  }
  const level = showing || shownLevel(ref, state);
  if (level) {
    const stored = el("a", "sh-v-stored", ref.source.vault ? "note ↗" : `stored ↗ ${day(ref.source.retrieved_at) || level}`);
    stored.href = new URL(`stored/${encodeURIComponent(ref.id)}/${level}`, base).href;
    stored.target = "_blank";
    stored.rel = "noopener";
    stored.title = `The stored ${level} this mark is drawn on`;
    node.append(stored);
  } else {
    node.append(el("span", "sh-v-muted", "stored · none"));
  }
  return node;
}

/** The "nothing stored" state: the URL and the quote, never a guessed mark. */
export function emptyState(ref, state, base) {
  const box = el("div", "sh-v-empty");
  const inner = el("div", "sh-v-empty-in");
  inner.append(el("b", "", "Nothing stored for this reference, so it can’t show where."));
  if (ref.quote) inner.append(el("q", "", ref.quote));
  if (ref.where) inner.append(el("p", "sh-v-where", ref.where));
  inner.append(el("p", "", "Open the live page to look yourself; capture it to keep a copy."), links(ref, state, base));
  box.append(inner);
  return box;
}

/**
 * Drag `handle`: onMove(dx, dy, starting, begin) is called with starting = true once (its return value comes back
 * as `begin` on every move), then with the pointer's offset; onDone() when the pointer is released (save there).
 * WHY pointer capture: the drag keeps going when the pointer leaves the thin handle.
 */
export function draggable(handle, { onMove, onDone }) {
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button, a, select")) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    document.documentElement.classList.add("sh-v-dragging");
    const startX = event.clientX;
    const startY = event.clientY;
    const begin = onMove(0, 0, true);
    const move = (moveEvent) => onMove(moveEvent.clientX - startX, moveEvent.clientY - startY, false, begin);
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      document.documentElement.classList.remove("sh-v-dragging");
      onDone?.();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
}
