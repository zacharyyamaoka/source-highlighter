"""The CONTRACT §6 status check: is each cited source still exactly what was cited?

`source-highlighter check refs.json [--out statuses.json]` fetches each live source once, politely (fetch.py), and
answers per Reference in Wikipedia's url-status words:

- live      — the same sha256 for a file; for a web page, every html Location resolves by §2 and the quote is
              found exactly once (an approximate find is not exact);
- deviated  — reachable, but no longer an exact match (and why: the quote is gone, appears twice, the file changed);
- dead      — unreachable three times, the checks spread over time (InternetArchiveBot's rule);
- unreachable (n of 3) — unreachable, not yet three times;
- link-only — nothing stored and no exact live match.

Each status carries the live link the browser highlights itself (a Text Fragment for HTML, #page=N for a PDF).
The check never draws anything: the viewer marks a stored copy, always (§6).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import unicodedata
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .fetch import Fetcher, FetchError, FetchRefused, media_type_of
from .refs import is_local, kept_levels, local_path, vault_path
from .render import decode_html

DEAD_AFTER = 3
# WHY a day between counted failures: "three checks spread over time" (InternetArchiveBot) is about outages, not
# retries — three failures inside one bad hour say nothing about whether the page is gone.
FAILURE_SPACING_S = 24 * 3600
QUOTE_ID = "__quote__"


def iso_z(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_of(iso: str) -> float:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


def day(iso: str | None) -> str:
    return (iso or "")[:10]


def text_fragment_link(url: str, quote: str) -> str:
    """`url#:~:text=…`, so the browser itself scrolls to and highlights the quote on the live page.

    WHY start,end for several words: a cited quote often runs across table cells ("Rated torque (Nm) 9"), and a
    Text Fragment's single term cannot cross a block boundary; a range from the first words to the last can.
    """
    words = quote.split()
    if not words:
        return url
    encode = lambda term: urllib.parse.quote(term, safe="").replace("-", "%2D")
    directive = encode(words[0]) if len(words) == 1 else f"{encode(' '.join(words[:-1]))},{encode(words[-1])}"
    return f"{url.split('#')[0]}#:~:text={directive}"


def live_link(ref: dict, media_type: str | None) -> str | None:
    url = ref["source"].get("url")
    if not url:
        return None
    pdf = (media_type or ref["source"].get("media_type") or "") == "application/pdf" or url.lower().split("?")[0].endswith(".pdf")
    if pdf:
        page = next((location["page"] for location in ref.get("locations") or [] if isinstance(location.get("page"), int)), None)
        return f"{url.split('#')[0]}#page={page}" if page else url
    quote = ref.get("quote") or next(
        (location["html"]["text_quote"]["exact"] for location in ref.get("locations") or [] if (location.get("html") or {}).get("text_quote", {}).get("exact")),
        None,
    )
    return text_fragment_link(url, quote) if quote else url


def radical_variants() -> dict[str, str]:
    """CJK Radicals Supplement (U+2E80–2EF3) characters with no NFKC decomposition → the Kangxi radical's ideograph that
    shares their Unicode name; js/source-highlighter.js carries the same table (tests/test_quote_fold.py pins them equal)."""
    kangxi = {unicodedata.name(chr(code))[len("KANGXI RADICAL "):]: unicodedata.normalize("NFKC", chr(code)) for code in range(0x2F00, 0x2FD6)}
    variants = {}
    for code in range(0x2E80, 0x2EF4):
        name = unicodedata.name(chr(code), "")
        if unicodedata.normalize("NFKC", chr(code)) == chr(code) and name[len("CJK RADICAL "):] in kangxi:
            variants[chr(code)] = kangxi[name[len("CJK RADICAL "):]]
    return variants


_RADICAL_VARIANTS = radical_variants()


def _squash(text: str) -> str:
    """`text` folded for matching (NFKC + radical variants, as the JS quote find does) and without whitespace.
    WHY fold: Chromium-printed PDFs put Kangxi-radical lookalikes in the text layer (⽐ for 比); counting a quote without
    the fold said 'gone' for text that is on the page. Comparison only: nothing stored or shown is folded."""
    folded = unicodedata.normalize("NFKC", "".join(_RADICAL_VARIANTS.get(char, char) for char in text))
    return re.sub(r"\s+", "", folded)


def pdf_quote_count(data: bytes, quote: str, page: int | None = None) -> int:
    """How often `quote` occurs in a PDF's text (pdfium), whitespace ignored as in the module's quote find."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    try:
        pages = [page] if page else range(1, len(document) + 1)
        needle = _squash(quote)
        count = 0
        for number in pages:
            if not 1 <= number <= len(document):
                continue
            textpage = document[number - 1].get_textpage()
            count += _squash(textpage.get_text_range()).count(needle) if needle else 0
        return count
    finally:
        document.close()


class _Observation:
    """What fetching (or reading) one source found today."""

    def __init__(self, *, body: bytes | None = None, media_type: str | None = None, http_status: int | None = None,
                 failure: str | None = None, refused: str | None = None):
        self.body, self.media_type, self.http_status, self.failure, self.refused = body, media_type, http_status, failure, refused


def _observe(ref: dict, fetcher: Fetcher, vault: Path | None, seen: dict) -> _Observation:
    source = ref["source"]
    if source.get("vault"):
        if vault is None:
            return _Observation(refused="no vault root given (--vault), so the note can't be read")
        path = vault_path(ref, vault)
        if path is None or not path.is_file():
            return _Observation(failure=f"the vault note {source['vault']} is missing")
        return _Observation(body=path.read_bytes(), media_type="text/markdown")
    url = source["url"]
    if url in seen:  # one request per URL per run, however many References cite it
        return seen[url]
    if is_local(url):
        path = local_path(url)
        observation = _Observation(body=path.read_bytes(), media_type=media_type_of(None, path.read_bytes())) if path.is_file() else _Observation(failure=f"{path} is missing")
    else:
        try:
            fetched = fetcher.fetch(url)
            observation = _Observation(body=fetched.body, media_type=media_type_of(fetched.content_type, fetched.body), http_status=fetched.status)
        except FetchRefused as refused:
            observation = _Observation(refused=str(refused))
        except FetchError as error:
            observation = _Observation(failure=str(error), http_status=error.status)
    seen[url] = observation
    return observation


def _html_job(ref: dict, identical: bool) -> list[dict] | None:
    """The Locations to resolve on today's HTML: html ones (source_range dropped once the bytes differ — it counts
    into the cited bytes) and the quote."""
    locations = []
    for index, location in enumerate(ref.get("locations") or []):
        if "html" not in location:
            continue
        html = copy.deepcopy(location["html"])
        if not identical:
            html.pop("source_range", None)
        if html:
            locations.append({"id": f"loc:{index}", "html": html})
    if ref.get("quote"):
        locations.append({"id": QUOTE_ID, "quote": ref["quote"]})
    return locations or None


def _quote_verdict(result: dict) -> tuple[bool, str | None]:
    """(found exactly once, else why not) from the module's resolution of the QUOTE_ID Location."""
    for entry in result["unresolved"]:
        if (entry.get("location") or {}).get("id") == QUOTE_ID:
            reason = entry["reason"]
            if reason.startswith("ambiguous"):
                count = re.search(r"occurs (\d+) times", reason)
                return False, f"the quote appears {count.group(1)} times" if count else f"the quote is {reason[len('ambiguous: '):]}"
            return False, "the quote is gone"
    found = next((mark.get("found") for mark in result["marks"] if mark["id"] == QUOTE_ID), None)
    if found and not found["exact"]:
        return False, f"the quote is found only approximately ({found['errors']} edit{'s' if found['errors'] != 1 else ''}): now “{found['text']}”"
    return True, None


def _location_problems(ref: dict, result: dict) -> list[str]:
    problems = []
    for entry in result["unresolved"]:
        location_id = (entry.get("location") or {}).get("id", "")
        if location_id.startswith("loc:"):
            original = (ref.get("locations") or [])[int(location_id[4:])]
            problems.append(f"location {original.get('id', location_id[4:])} no longer resolves ({entry['reason']})")
    return problems


def _failures(previous: dict | None, now: float) -> list[str]:
    """This failure added to the earlier ones, counted only when spread FAILURE_SPACING_S apart."""
    history = list((previous or {}).get("failures") or [])
    if not history or now - epoch_of(history[-1]) >= FAILURE_SPACING_S:
        history.append(iso_z(now))
    return history


def check_references(refs: list[dict], *, base: Path, vault: Path | None = None, previous: dict | None = None,
                     fetcher: Fetcher | None = None, now: float | None = None) -> dict[str, Any]:
    """The §6 status of every Reference. `previous` is an earlier result (its failure history counts toward dead)."""
    from .browser import resolve_in_browser

    now = time.time() if now is None else now
    fetcher = fetcher or Fetcher()
    earlier = (previous or {}).get("statuses") or {}
    seen: dict[str, _Observation] = {}
    observations = {ref["id"]: _observe(ref, fetcher, vault, seen) for ref in refs}

    # Every HTML page and vault note is resolved by the module, in one browser.
    jobs: list[tuple[str, str, list[dict]]] = []
    job_of: dict[str, int] = {}
    for ref in refs:
        observation = observations[ref["id"]]
        if observation.body is None:
            continue
        identical = bool(ref["source"].get("sha256")) and hashlib.sha256(observation.body).hexdigest() == ref["source"]["sha256"]
        if observation.media_type == "text/markdown":
            notes = [{"id": f"loc:{index}", "note": location["note"]} for index, location in enumerate(ref.get("locations") or []) if "note" in location]
            if ref.get("quote"):
                notes.append({"id": QUOTE_ID, "quote": ref["quote"]})
            if notes:
                job_of[ref["id"]] = len(jobs)
                jobs.append(("text", observation.body.decode("utf-8"), notes))
        elif observation.media_type == "text/html" and not identical:
            locations = _html_job(ref, identical)
            if locations:
                job_of[ref["id"]] = len(jobs)
                jobs.append(("html", decode_html(observation.body), locations))
    resolved = resolve_in_browser(jobs) if jobs else []

    statuses = {}
    for ref in refs:
        observation = observations[ref["id"]]
        source = ref["source"]
        stored = bool(source.get("vault")) or bool(kept_levels(ref, base))
        since = day(source.get("retrieved_at"))
        entry: dict[str, Any] = {"checked_at": iso_z(now), "url": source.get("url"), "live_link": live_link(ref, observation.media_type)}
        if observation.http_status is not None:
            entry["http_status"] = observation.http_status
        if observation.refused:
            entry.update(status="unreachable", label="not checked", why=observation.refused, failures=list((earlier.get(ref["id"]) or {}).get("failures") or []))
        elif observation.failure:
            failures = _failures(earlier.get(ref["id"]), now)
            dead = len(failures) >= DEAD_AFTER
            entry.update(
                status="dead" if dead else "unreachable",
                label="page unreachable" if dead else f"unreachable ({len(failures)} of {DEAD_AFTER})",
                why=f"{observation.failure}; failed on {', '.join(day(when) for when in failures)}",
                failures=failures,
            )
        else:
            entry["failures"] = []
            live_sha = hashlib.sha256(observation.body).hexdigest()
            entry["live_sha256"] = live_sha
            entry["media_type"] = observation.media_type
            problems: list[str] = []
            if source.get("vault"):
                result = resolved[job_of[ref["id"]]] if ref["id"] in job_of else {"marks": [], "unresolved": []}
                problems = _location_problems(ref, result)
                if ref.get("quote"):
                    exact, why = _quote_verdict(result)
                    problems += [] if exact else [why]
                is_live, why = not problems, "vault note: the vault is the copy" + (f"; {'; '.join(problems)}" if problems else "")
            elif source.get("sha256") and live_sha == source["sha256"]:
                is_live, why = True, "byte-identical to the cited copy (same sha256)"
            elif observation.media_type != "text/html":
                if source.get("sha256"):
                    is_live, why = False, "the live file's sha256 no longer matches the cited one"
                elif ref.get("quote") and observation.media_type == "application/pdf":
                    count = pdf_quote_count(observation.body, ref["quote"])
                    is_live = count == 1
                    why = "no sha256 was cited; the quote is found exactly once in the PDF" if is_live else f"no sha256 was cited, and the quote appears {count} times in the PDF"
                else:
                    is_live, why = False, "no sha256 or quote was cited, so an exact match can't be shown"
            elif ref["id"] in job_of:
                result = resolved[job_of[ref["id"]]]
                problems = _location_problems(ref, result)
                if ref.get("quote"):
                    exact, quote_why = _quote_verdict(result)
                    if not exact:
                        problems.append(quote_why)
                is_live = not problems
                if is_live:
                    has_locations = any("html" in location for location in ref.get("locations") or [])
                    why = ("every location resolves on the live page" + (" and " if ref.get("quote") else "")) if has_locations else ""
                    why += "the quote is found exactly once" if ref.get("quote") else ""
                    why = why.strip() + (" (the bytes differ from the cited copy)" if source.get("sha256") else "")
                else:
                    why = "; ".join(problems)
            else:
                is_live, why = False, "the page changed (sha256) and the reference holds no quote or html location to re-find"
            if is_live:
                label = "vault note · the vault is the copy" if source.get("vault") else f"live · unchanged since {since}" if since else "live · unchanged"
                if not stored:
                    why += "; nothing stored, so if the page changes there is no copy to fall back on"
                entry.update(status="live", label=label, why=why)
            elif not stored:
                entry.update(status="link-only", label="nothing stored; can't show where", why=why)
            else:
                entry.update(status="deviated", label=f"page changed since {since}" if since else "page changed", why=why)
        statuses[ref["id"]] = entry
    return {"checked_at": iso_z(now), "statuses": statuses}


def run(refs_path: Path, out: Path | None, vault: Path | None, *, fetcher: Fetcher | None = None, now: float | None = None) -> dict:
    from .refs import load_refs

    refs = load_refs(refs_path)
    previous = json.loads(out.read_text(encoding="utf-8")) if out is not None and out.is_file() else None
    result = check_references(refs, base=refs_path.resolve().parent, vault=vault, previous=previous, fetcher=fetcher, now=now)
    if out is not None:
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result
