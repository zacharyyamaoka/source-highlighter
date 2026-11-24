"""The §6 status check against a local HTTP fixture server: unchanged, moved-but-same, changed quote, quote twice,
file sha changed, 404 (three times, spread over days, before it is dead), robots.txt, and the pace per host."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from source_highlighter.check import FAILURE_SPACING_S, run, text_fragment_link
from source_highlighter.cli import main
from source_highlighter.fetch import Fetcher

from .conftest import FIXTURES

CITED = (
    "<!doctype html><html><body><h1>AK80-9</h1><table>"
    "<tr><td>Rated torque (Nm)</td><td>9</td></tr>"
    "<tr><td>Peak torque (Nm)</td><td>22</td></tr>"
    "</table></body></html>"
)
NINE = CITED.index("<td>9</td>") + len("<td>")
LOCATION = {
    "id": "rated-torque",
    "html": {
        "source_range": {"start": NINE, "end": NINE + 1},
        "xpath": "/html/body/table/tr[1]/td[2]",
        "text_quote": {"exact": "9", "prefix": "Rated torque (Nm)", "suffix": "Peak torque"},
    },
}
QUOTE = "Rated torque (Nm) 9"
PDF = (FIXTURES / "lrs-350.pdf").read_bytes()
ROW = "<tr><td>Rated torque (Nm)</td><td>9</td></tr>"
PAGES = {
    "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /private/\n"),
    "/unchanged.html": (200, "text/html; charset=utf-8", CITED.encode()),
    # new bytes (a banner, a class), same table: every location still resolves, the quote is found once
    "/moved.html": (200, "text/html; charset=utf-8", CITED.replace("<h1>", "<p>Sale!</p><h1 class='x'>").encode()),
    "/changed.html": (200, "text/html; charset=utf-8", CITED.replace("<td>9</td>", "<td>8</td>").encode()),
    "/twice.html": (200, "text/html; charset=utf-8", CITED.replace(ROW, ROW + ROW).encode()),
    "/same.pdf": (200, "application/pdf", PDF),
    "/changed.pdf": (200, "application/pdf", PDF + b"\n% re-issued\n"),
    "/private/page.html": (200, "text/html", CITED.encode()),
}


class _Handler(BaseHTTPRequestHandler):
    hits: ClassVar[list[str]] = []

    def do_GET(self):
        _Handler.hits.append(self.path)
        status, content_type, body = PAGES.get(self.path, (404, "text/html", b"<h1>Not found</h1>"))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    _Handler.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class FakeTime:
    """The fetcher's clock and sleep: pacing is asserted on, not waited for."""

    def __init__(self, start: float):
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def write_refs(tmp_path, site) -> tuple:
    stored = tmp_path / "stored.html"
    stored.write_text(CITED, encoding="utf-8")
    stored_pdf = tmp_path / "stored.pdf"
    stored_pdf.write_bytes(PDF)
    html_sha = hashlib.sha256(CITED.encode()).hexdigest()
    pdf_sha = hashlib.sha256(PDF).hexdigest()

    def html_ref(ref_id, path, **extra):
        source = {"url": site + path, "retrieved_at": "2026-10-29T19:44:27Z", "sha256": html_sha, "media_type": "text/html", "stored": {"html": str(stored)}}
        return {"id": ref_id, "source": source, "locations": [LOCATION], "quote": QUOTE, **extra}

    refs = [
        html_ref("unchanged", "/unchanged.html"),
        html_ref("moved", "/moved.html"),
        html_ref("changed-quote", "/changed.html"),
        html_ref("quote-twice", "/twice.html"),
        html_ref("gone", "/gone.html"),
        html_ref("private", "/private/page.html"),
        {"id": "pdf-same", "source": {"url": site + "/same.pdf", "sha256": pdf_sha, "retrieved_at": "2026-10-29T19:44:27Z", "stored": {"pdf": "stored.pdf"}},
         "locations": [{"page": 2, "box": [0.67946, 0.12915, 0.7325, 0.14301]}], "quote": "350.4W"},
        {"id": "pdf-changed", "source": {"url": site + "/changed.pdf", "sha256": pdf_sha, "retrieved_at": "2026-10-29T19:44:27Z", "stored": {"pdf": "stored.pdf"}},
         "locations": [{"page": 2, "box": [0.67946, 0.12915, 0.7325, 0.14301]}]},
        # nothing stored: live only on an exact live match, else link-only
        {"id": "quote-only", "source": {"url": site + "/unchanged.html", "stored": {}}, "locations": [], "quote": "Peak torque (Nm) 22"},
        {"id": "quote-only-gone", "source": {"url": site + "/changed.html", "stored": {}}, "locations": [], "quote": QUOTE},
        {"id": "link-only", "source": {"url": site + "/moved.html", "stored": {}}, "locations": []},
        {"id": "note", "source": {"vault": "Bench notes.md"}, "locations": [{"note": {"heading": "Torque"}}]},
        {"id": "note-gone", "source": {"vault": "Bench notes.md"}, "locations": [{"note": {"heading": "Speed"}}]},
    ]
    refs_path = tmp_path / "refs.json"
    refs_path.write_text(json.dumps(refs), encoding="utf-8")
    return refs_path, tmp_path / "statuses.json"


def test_each_case_gets_its_status_word_and_why(site, tmp_path):
    refs_path, out = write_refs(tmp_path, site)
    clock = FakeTime(1_800_000_000.0)
    result = run(refs_path, out, FIXTURES / "vault", fetcher=Fetcher(clock=clock.clock, sleep=clock.sleep), now=clock.now)
    statuses = result["statuses"]
    assert json.loads(out.read_text()) == result
    words = {ref_id: entry["status"] for ref_id, entry in statuses.items()}
    assert words == {
        "unchanged": "live",
        "moved": "live",
        "changed-quote": "deviated",
        "quote-twice": "deviated",
        "gone": "unreachable",
        "private": "unreachable",
        "pdf-same": "live",
        "pdf-changed": "deviated",
        "quote-only": "live",
        "quote-only-gone": "link-only",
        "link-only": "link-only",
        "note": "live",
        "note-gone": "deviated",
    }
    assert "same sha256" in statuses["unchanged"]["why"]
    assert statuses["unchanged"]["label"] == "live · unchanged since 2026-10-29"
    assert "every location resolves" in statuses["moved"]["why"] and "found exactly once" in statuses["moved"]["why"]
    assert "approximately" in statuses["changed-quote"]["why"] and "Rated torque (Nm)8" in statuses["changed-quote"]["why"]
    assert statuses["changed-quote"]["label"] == "page changed since 2026-10-29"
    assert "appears 2 times" in statuses["quote-twice"]["why"]
    assert statuses["gone"]["label"] == "unreachable (1 of 3)" and "HTTP 404" in statuses["gone"]["why"]
    assert statuses["gone"]["http_status"] == 404
    assert "robots.txt disallows" in statuses["private"]["why"] and statuses["private"]["failures"] == []
    assert "sha256 no longer matches" in statuses["pdf-changed"]["why"]
    assert "nothing stored" in statuses["quote-only"]["why"]
    assert "no longer resolves" in statuses["note-gone"]["why"]
    for entry in statuses.values():
        assert entry["checked_at"] == "2027-01-15T08:00:00Z"
    # the live side is a link the browser highlights itself
    assert statuses["unchanged"]["live_link"] == site + "/unchanged.html#:~:text=Rated%20torque%20%28Nm%29,9"
    # a PDF opens at its page with the cited words searched (#page=N&search="…"&phrase=true, test_pdf_open_link_*)
    assert statuses["pdf-same"]["live_link"] == site + "/same.pdf#page=2&search=%22350.4W%22&phrase=true"
    assert statuses["pdf-changed"]["live_link"] == site + "/changed.pdf#page=2"  # no quote: the page only
    assert statuses["note"]["live_link"] is None


def test_polite_one_request_per_url_and_a_second_between_requests_to_a_host(site, tmp_path):
    refs_path, out = write_refs(tmp_path, site)
    clock = FakeTime(1_800_000_000.0)
    fetcher = Fetcher(clock=clock.clock, sleep=clock.sleep)
    run(refs_path, out, FIXTURES / "vault", fetcher=fetcher, now=clock.now)
    paths = [url.removeprefix(site) for _, url in fetcher.requests]
    assert paths.count("/robots.txt") == 1
    assert sorted(paths) == sorted(set(paths))  # /unchanged.html and /moved.html are each cited twice, fetched once
    assert "/private/page.html" not in _Handler.hits  # robots.txt said no, so it was never asked for
    gaps = [later - earlier for (earlier, _), (later, _) in zip(fetcher.requests, fetcher.requests[1:])]
    assert gaps and min(gaps) >= 1.0


def test_dead_needs_three_failures_spread_over_time(site, tmp_path):
    refs_path, out = write_refs(tmp_path, site)
    refs = json.loads(refs_path.read_text())
    refs_path.write_text(json.dumps([ref for ref in refs if ref["id"] == "gone"]))
    start = 1_800_000_000.0

    def check_at(when):
        clock = FakeTime(when)
        return run(refs_path, out, None, fetcher=Fetcher(clock=clock.clock, sleep=clock.sleep), now=when)["statuses"]["gone"]

    assert check_at(start)["label"] == "unreachable (1 of 3)"
    assert check_at(start + 3600)["label"] == "unreachable (1 of 3)"  # a retry an hour later is the same outage
    second = check_at(start + FAILURE_SPACING_S)
    assert (second["status"], second["label"]) == ("unreachable", "unreachable (2 of 3)")
    third = check_at(start + 2 * FAILURE_SPACING_S)
    assert (third["status"], third["label"]) == ("dead", "page unreachable")
    assert len(third["failures"]) == 3


def test_a_success_clears_the_failure_history(site, tmp_path):
    refs_path, out = write_refs(tmp_path, site)
    out.write_text(json.dumps({"statuses": {"unchanged": {"failures": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]}}}))
    result = run(refs_path, out, FIXTURES / "vault", fetcher=Fetcher(clock=FakeTime(1e9).clock, sleep=lambda s: None))
    assert result["statuses"]["unchanged"]["failures"] == []


def test_text_fragment_escapes_its_own_syntax():
    assert text_fragment_link("https://x.test/a#top", "88%") == "https://x.test/a#:~:text=88%25"
    assert text_fragment_link("https://x.test/a", "LRS-350, 24 V & more") == "https://x.test/a#:~:text=LRS%2D350%2C%2024%20V%20%26,more"


def test_cli_check_writes_statuses(site, tmp_path, capsys):
    refs_path, out = write_refs(tmp_path, site)
    refs = json.loads(refs_path.read_text())
    refs_path.write_text(json.dumps([ref for ref in refs if ref["id"] in ("unchanged", "note")]))
    assert main(["check", str(refs_path), "--out", str(out), "--vault", str(FIXTURES / "vault")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(out.read_text())
    assert {entry["status"] for entry in printed["statuses"].values()} == {"live"}


def test_pdf_open_link_searches_the_quote_on_its_page():
    """PDF open parameters (RFC 8118 / Adobe) + pdf.js's phrase=true; the quote is encoded whole so & # + survive."""
    from source_highlighter.check import live_link, pdf_open_link

    assert pdf_open_link("https://x.test/a.pdf#old", 2, "350.4W") == "https://x.test/a.pdf#page=2&search=%22350.4W%22&phrase=true"
    assert pdf_open_link("https://x.test/a.pdf", 2, "Rated & peak #1 +5") == \
        "https://x.test/a.pdf#page=2&search=%22Rated%20%26%20peak%20%231%20%2B5%22&phrase=true"
    assert pdf_open_link("https://x.test/a.pdf", None, "88.5%") == "https://x.test/a.pdf#search=%2288.5%25%22&phrase=true"
    assert pdf_open_link("https://x.test/a.pdf", 3, None) == "https://x.test/a.pdf#page=3"
    assert pdf_open_link("https://x.test/a.pdf", None, "  ") == "https://x.test/a.pdf"
    # the page + quote Location's own words win over the reference's quote
    ref = {"source": {"url": "https://x.test/a.pdf"}, "locations": [{"page": 2, "quote": "349.2W"}], "quote": "Rated power 349.2W"}
    assert live_link(ref, None) == "https://x.test/a.pdf#page=2&search=%22349.2W%22&phrase=true"
    # what pdf.js reads back from that fragment: the words, quotes stripped, as one phrase
    import urllib.parse

    fragment = urllib.parse.urlsplit(live_link(ref, None)).fragment
    params = dict(urllib.parse.parse_qsl(fragment))
    assert params["search"].replace('"', "") == "349.2W" and params["phrase"] == "true" and params["page"] == "2"
