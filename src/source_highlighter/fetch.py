"""Polite HTTP for the §6 status check: robots.txt first, at most one request per second per host, and a few hosts
it never touches.

Copied from Markdownify's `src/markdownify/fetch.py` (same author, /home/bam/markdownify), which adapted robotparts'
`tools/ingestor/fetch.py`; the rules are unchanged. WHY these rules (Zach's research note "Viam and Full Stack
Component Prior Art for robotparts", 2026-10-22, and CONTRACT §6):
- robots.txt is honoured for our user agent, and a robots.txt that answers 401/403 or 5xx means "no";
- one request per second per host at most, raised to the site's own Crawl-delay when it asks for more;
- McMaster-Carr is never fetched (no scraping at all), and neither is Octopart/Nexar;
- redirects are followed by hand, so each hop is checked against its own host's robots.txt and pace.

What changed from Markdownify: nothing is cached (a check wants today's bytes), so robots.txt and each host's last
request are remembered in memory for one run — a check is one process — and the user agent names this tool.
"""

from __future__ import annotations

import dataclasses
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

from . import __version__

USER_AGENT = f"source-highlighter/{__version__} (checks that cited sources still say what was cited, +https://robotparts.dev/)"
ROBOTS_AGENT = "source-highlighter"
MIN_INTERVAL_S = 1.0
TIMEOUT_S = 30.0
MAX_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 5
REFUSED_HOSTS = {
    "mcmaster.com": "McMaster-Carr is never scraped (robotparts research note, 2026-10-22)",
    "octopart.com": "Octopart/Nexar data may not be stored beyond 24 h (nexar.com/api/legal)",
    "nexar.com": "Octopart/Nexar data may not be stored beyond 24 h (nexar.com/api/legal)",
}


class FetchRefused(Exception):
    """The fetcher will not request this URL (a refused host, or robots.txt says no)."""


class FetchError(Exception):
    """The request was made and failed (HTTP error, network error, too large)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclasses.dataclass
class Fetched:
    url: str  # the URL that was asked for (what a citation cites)
    final_url: str  # where the bytes came from after redirects
    status: int
    content_type: str | None
    body: bytes


def host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def normalize_url(url: str) -> str:
    """Percent-encode what a URL may not carry raw (spaces, non-ASCII), keeping existing escapes."""
    parts = urllib.parse.urlsplit(url.strip())
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parts.query, safe="=&%:@!$'()*+,;/?-._~")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def refused_reason(url: str) -> str | None:
    host = host_of(url)
    for domain, reason in REFUSED_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return reason
    return None


def media_type_of(content_type: str | None, data: bytes) -> str:
    """The media type, sniffed from the bytes first (a PDF served as octet-stream is a PDF)."""
    head = data[:1024]
    if b"%PDF-" in head:
        return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in ("text/html", "application/xhtml+xml"):
        return "text/html"
    lowered = data[:4096].lower()
    if b"<html" in lowered or b"<!doctype html" in lowered:
        return "text/html"
    return declared or "application/octet-stream"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Fetcher:
    def __init__(self, *, user_agent: str = USER_AGENT, min_interval: float = MIN_INTERVAL_S, timeout: float = TIMEOUT_S,
                 clock=time.time, sleep=time.sleep, log=None):
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.timeout = timeout
        self.clock = clock
        self.sleep = sleep
        self.log = log or (lambda message: None)
        self.requests: list[tuple[float, str]] = []  # (when, url) of every request made, robots.txt included
        self._opener = urllib.request.build_opener(_NoRedirect())
        self._robots: dict[str, tuple[urllib.robotparser.RobotFileParser, float]] = {}
        self._last: dict[str, float] = {}

    def fetch(self, url: str) -> Fetched:
        """Today's bytes of `url`. Raises FetchRefused (not allowed to ask) or FetchError (asked, and it failed)."""
        if not url.startswith(("http://", "https://")):
            raise FetchRefused(f"{url!r} is not an http(s) URL")
        url = normalize_url(url)
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            reason = refused_reason(current)
            if reason:
                raise FetchRefused(f"{host_of(current)}: {reason}" + ("" if current == url else f" (redirected from {url})"))
            allowed, why = self.allowed(current)
            if not allowed:
                raise FetchRefused(why)
            status, headers, body = self._request(current, MAX_BYTES)
            if status in (301, 302, 303, 307, 308):
                location = headers.get("Location")
                if not location:
                    raise FetchError(f"{current}: HTTP {status} without a Location", status)
                current = normalize_url(urllib.parse.urljoin(current, location))
                continue
            if status != 200:
                raise FetchError(f"HTTP {status}", status)
            return Fetched(url=url, final_url=current, status=status, content_type=headers.get("Content-Type"), body=body)
        raise FetchError(f"more than {MAX_REDIRECTS} redirects")

    def allowed(self, url: str) -> tuple[bool, str]:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser, _pace = self._robots_for(origin)
        if parser.can_fetch(ROBOTS_AGENT, url):
            return True, "allowed by robots.txt"
        return False, f"{origin}/robots.txt disallows {url} for {ROBOTS_AGENT}"

    # --- robots.txt ----------------------------------------------------------------------------------
    def _robots_for(self, origin: str) -> tuple[urllib.robotparser.RobotFileParser, float]:
        if origin in self._robots:
            return self._robots[origin]
        status, text = self._read_robots(origin)
        parser = urllib.robotparser.RobotFileParser()
        if status in (401, 403) or status is None or status >= 500:
            # WHY: RFC 9309 — an unreachable or forbidden robots.txt means the site has not allowed us in.
            parser.disallow_all = True
        elif 400 <= status < 500:
            parser.allow_all = True
        else:
            parser.parse(text.splitlines())
        parser.modified()
        delay = parser.crawl_delay(ROBOTS_AGENT)
        rate = parser.request_rate(ROBOTS_AGENT)
        pace = max(self.min_interval, float(delay or 0), (rate.seconds / rate.requests) if rate else 0.0)
        self._robots[origin] = (parser, pace)
        return parser, pace

    def _read_robots(self, origin: str) -> tuple[int | None, str]:
        current = origin + "/robots.txt"
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                status, headers, body = self._request(current, 512 * 1024, robots=True)
                if status in (301, 302, 303, 307, 308) and headers.get("Location"):
                    current = urllib.parse.urljoin(current, headers["Location"])
                    continue
                return status, body.decode("utf-8", errors="replace") if status == 200 else ""
        except (FetchError, OSError):
            pass
        return None, ""

    # --- one request ---------------------------------------------------------------------------------
    def _turn(self, host: str, pace: float) -> None:
        """Wait until `pace` seconds have passed since this host's last request."""
        last = self._last.get(host)
        if last is not None:
            wait = last + pace - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def _request(self, url: str, max_bytes: int, robots: bool = False) -> tuple[int, dict, bytes]:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        pace = self.min_interval if robots else self._robots_for(f"{parsed.scheme}://{parsed.netloc}")[1]
        self._turn(host, pace)
        self.requests.append((self.clock(), url))
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "*/*"})
        status, headers, body, error = None, {}, b"", None
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                headers = dict(response.headers.items())
                body = response.read(max_bytes + 1)
        except urllib.error.HTTPError as http_error:
            status = http_error.code
            headers = dict(http_error.headers.items()) if http_error.headers else {}
            body = b""
        except (urllib.error.URLError, OSError, ValueError) as network_error:
            # WHY ValueError: http.client refuses a malformed URL with InvalidURL, a ValueError.
            error = str(getattr(network_error, "reason", network_error))
        if error is not None:
            raise FetchError(error)
        if len(body) > max_bytes:
            raise FetchError(f"larger than {max_bytes} bytes", status)
        self.log(f"GET {url} -> {status} ({len(body)} bytes)")
        return status, headers, body
