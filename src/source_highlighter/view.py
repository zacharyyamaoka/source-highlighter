"""`source-highlighter view refs.json [--port 0]`: the standalone viewer for a reference set, on 127.0.0.1.

- `/` is **Lens**: one Reference full page (`#ref=<id>`), its stored copy marked, ‹ › through the set, and a
  Details panel with the §6 status, both links, what was kept and the fallback chain.
- `/peek.js` is **Peek**: `<script type="module" src="…/peek.js" data-refs="refs.json">` in any page turns each
  `<span data-ref="id">` into a citation that peeks a crop on hover and pins the source on click.

The server only hands out the files the reference set names (`/stored/<id>/<level>`), never an arbitrary path,
and serves an archived page with a sandbox CSP so opening it can't run its scripts. The status comes from
`source-highlighter check` (a statuses.json); the viewer never fetches a live page (CONTRACT §6).
"""

from __future__ import annotations

import json
import mimetypes
import re
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from .refs import LEVELS, load_refs, stored_path, vault_path

CHECKOUT = Path(__file__).resolve().parents[2]
LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")
ARCHIVED_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"


def _asset_dir(name: str) -> Path:
    """viewer/ or js/: the copy inside an installed wheel, else the checkout's."""
    packaged = resources.files("source_highlighter").joinpath(name)
    return Path(str(packaged)) if packaged.is_dir() else CHECKOUT / name


def _pdfjs_dir() -> Path | None:
    for candidate in (_asset_dir("js") / "node_modules" / "pdfjs-dist" / "build", CHECKOUT / "js" / "node_modules" / "pdfjs-dist" / "build"):
        if (candidate / "pdf.min.mjs").is_file():
            return candidate
    return None


class Viewer:
    """What the server serves: the reference set, its statuses, and the files they name."""

    def __init__(self, refs_path: Path, statuses: Path | None, vault: Path | None):
        self.refs_path = refs_path.resolve()
        self.base = self.refs_path.parent
        beside = self.base / "statuses.json"
        self.statuses_path = statuses.resolve() if statuses else (beside if beside.is_file() else None)
        self.vault = vault.resolve() if vault else None
        load_refs(self.refs_path)  # fail at start, not on the first request

    def refs(self) -> list[dict]:
        return load_refs(self.refs_path)

    def statuses(self) -> dict:
        if self.statuses_path and self.statuses_path.is_file():
            return json.loads(self.statuses_path.read_text(encoding="utf-8"))
        return {}

    def file_of(self, ref: dict, level: str) -> Path | None:
        if level == "vault":
            return vault_path(ref, self.vault)
        if level in LEVELS:
            return stored_path(ref, level, self.base)
        return None

    def state(self) -> dict:
        """Per Reference, each stored level: kept or not, and its size — what Details shows."""
        files = {}
        for ref in self.refs():
            levels = {}
            for level in (*LEVELS, "vault"):
                path = self.file_of(ref, level)
                if path is not None:
                    levels[level] = {"kept": path.is_file(), "bytes": path.stat().st_size if path.is_file() else None}
            files[ref["id"]] = levels
        statuses = self.statuses()
        return {
            "files": files,
            "statuses": statuses.get("statuses", {}),
            "checked_at": statuses.get("checked_at"),
            "vault": str(self.vault) if self.vault else None,
            "pdfjs": _pdfjs_dir() is not None,
        }


def make_handler(viewer: Viewer):
    viewer_dir = _asset_dir("viewer")
    js_dir = _asset_dir("js")

    class Handler(BaseHTTPRequestHandler):
        server_version = "source-highlighter-view"

        def log_message(self, *args):
            pass

        def _cors(self) -> None:
            # WHY local origins only: a page on another localhost port (a CRM, mdify view) may embed Peek, but no
            # website the browser happens to have open may read the stored files.
            origin = self.headers.get("Origin")
            if origin and LOCAL_ORIGIN.match(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _file(self, path: Path | None, extra: dict | None = None) -> None:
            if path is None or not path.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix in (".js", ".mjs"):
                content_type = "text/javascript"
            if content_type.startswith("text/") and "charset" not in content_type:
                content_type += "; charset=utf-8"
            self._send(HTTPStatus.OK, path.read_bytes(), content_type, extra)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            try:
                if path in ("/", "/index.html"):
                    self._file(viewer_dir / "lens.html")
                elif re.fullmatch(r"/[a-z]+\.(js|css)", path):
                    self._file(viewer_dir / path[1:])
                elif path == "/source-highlighter.js" or re.fullmatch(r"/vendor/[\w.-]+\.js", path):
                    self._file(js_dir / path[1:])
                elif re.fullmatch(r"/pdfjs/pdf(\.worker)?\.min\.mjs", path):
                    pdfjs = _pdfjs_dir()
                    self._file(pdfjs / path.split("/")[-1] if pdfjs else None)
                elif path == "/refs.json":
                    self._file(viewer.refs_path)
                elif path == "/api/state":
                    self._send(HTTPStatus.OK, json.dumps(viewer.state(), ensure_ascii=False).encode(), "application/json")
                elif match := re.fullmatch(r"/stored/([^/]+)/([a-z]+)", path):
                    ref = next((ref for ref in viewer.refs() if ref["id"] == match.group(1)), None)
                    file = viewer.file_of(ref, match.group(2)) if ref else None
                    archived = file is not None and file.suffix.lower() in (".html", ".htm", ".xhtml")
                    self._file(file, {"Content-Security-Policy": ARCHIVED_CSP} if archived else None)
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")
            except (OSError, ValueError) as error:
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, str(error).encode(), "text/plain; charset=utf-8")

    return Handler


def make_server(refs_path: Path, *, port: int = 0, statuses: Path | None = None, vault: Path | None = None) -> ThreadingHTTPServer:
    viewer = Viewer(refs_path, statuses, vault)
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(viewer))


def serve(refs_path: Path, *, port: int = 0, statuses: Path | None = None, vault: Path | None = None) -> int:
    """Serve until interrupted. Prints the URL; opens no browser."""
    server = make_server(refs_path, port=port, statuses=statuses, vault=vault)
    print(f"http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print("source-highlighter view: stopped", file=sys.stderr)
    return 0
